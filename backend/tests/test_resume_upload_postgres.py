from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.api.v1.endpoints import resumes as resume_endpoint
from app.core.config import Settings, get_settings
from app.core.database import create_engine, get_db_session
from app.core.exceptions import APIError
from app.core.idempotency import RESUME_UPLOAD_ROUTE, derive_idempotent_resource_id
from app.core.security import create_access_token
from app.models.resume import Resume
from app.models.user import User
from app.services.resume_dispatcher import get_resume_parse_dispatcher
from app.services.resume_service import upload_resume
from app.storage.resume_storage import LocalResumeStorage
from main import app

TEST_SECRET = "resume-upload-postgres-jwt-secret"
PDF_BYTES = b"%PDF-1.7\n% real postgres resume upload\n%%EOF\n"


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, int]] = []

    async def dispatch(self, resume_id: uuid.UUID, revision: int) -> None:
        self.calls.append((resume_id, revision))


@dataclass
class PostgreSQLUploadHarness:
    client: AsyncClient
    session: AsyncSession
    user: User
    settings: Settings
    storage: LocalResumeStorage
    dispatcher: RecordingDispatcher
    outer_transaction: AsyncTransaction
    tracked_ids: set[uuid.UUID]

    def headers(self, key: uuid.UUID) -> dict[str, str]:
        token = create_access_token(self.user.id, self.settings)
        return {
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": str(key),
        }


def integration_database_settings() -> Settings:
    configured = Settings()
    if configured.database_url is None:
        pytest.skip("DATABASE_URL is not configured")
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=configured.database_url,
        database_connect_timeout_seconds=configured.database_connect_timeout_seconds,
    )


def make_integration_user(label: str) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=f"resume.upload.{label}.{uuid.uuid4().hex}@example.com",
        password_hash="not-used-by-resume-upload-integration",
        full_name="Resume Upload Integration",
        phone_number=None,
        role="CANDIDATE",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


@pytest_asyncio.fixture
async def postgres_upload(tmp_path: Path) -> AsyncIterator[PostgreSQLUploadHarness]:
    database_settings = integration_database_settings()
    application_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=None,
        jwt_secret_key=TEST_SECRET,
        resume_storage_root=tmp_path / "storage",
    )
    engine = create_engine(database_settings)
    assert engine is not None
    connection: AsyncConnection | None = None
    try:
        connection = await engine.connect()
    except Exception:  # noqa: BLE001 - never expose database credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)

    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    user = make_integration_user("api")
    user_id = user.id
    session.add(user)
    await session.commit()
    storage = LocalResumeStorage(application_settings.resume_storage_root)
    dispatcher = RecordingDispatcher()
    tracked_ids: set[uuid.UUID] = set()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    def override_settings() -> Settings:
        return application_settings

    def override_storage() -> LocalResumeStorage:
        return storage

    def override_dispatcher() -> RecordingDispatcher:
        return dispatcher

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[resume_endpoint.get_resume_storage] = override_storage
    app.dependency_overrides[get_resume_parse_dispatcher] = override_dispatcher
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield PostgreSQLUploadHarness(
                client,
                session,
                user,
                application_settings,
                storage,
                dispatcher,
                outer_transaction,
                tracked_ids,
            )
    finally:
        app.dependency_overrides.clear()
        remaining_users: int | None = None
        remaining_resumes: int | None = None
        try:
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()
            remaining_users = await connection.scalar(
                select(func.count()).select_from(User).where(User.id == user_id)
            )
            if tracked_ids:
                remaining_resumes = await connection.scalar(
                    select(func.count()).select_from(Resume).where(Resume.id.in_(tracked_ids))
                )
            else:
                remaining_resumes = 0
        finally:
            if connection.in_transaction():
                await connection.rollback()
            await connection.close()
            await engine.dispose()
        assert remaining_users == 0 and remaining_resumes == 0


@pytest.mark.asyncio
async def test_real_postgres_upload_fields_idempotency_conflict_and_usable_transaction(
    postgres_upload: PostgreSQLUploadHarness,
) -> None:
    harness = postgres_upload
    key = uuid.uuid4()
    expected_id = derive_idempotent_resource_id(harness.user.id, RESUME_UPLOAD_ROUTE, key)
    harness.tracked_ids.add(expected_id)
    first = await harness.client.post(
        "/api/v1/resumes/upload",
        headers=harness.headers(key),
        files={"file": ("integration.pdf", PDF_BYTES, "application/octet-stream")},
    )
    assert first.status_code == 202

    row = await harness.session.scalar(select(Resume).where(Resume.id == expected_id))
    assert row is not None
    assert row.id == expected_id
    assert row.owner_user_id == harness.user.id
    assert row.file_name == "integration.pdf"
    assert row.storage_key == f"resumes/{expected_id}/source"
    assert row.file_size == len(PDF_BYTES)
    assert row.mime_type == "application/pdf"
    assert row.create_request_fingerprint == hashlib.sha256(PDF_BYTES).hexdigest()
    assert row.revision == 1
    assert row.parsing_status == "PENDING"
    assert await harness.storage.read_bytes(row.storage_key) == PDF_BYTES

    retry = await harness.client.post(
        "/api/v1/resumes/upload",
        headers=harness.headers(key),
        files={"file": ("renamed.pdf", PDF_BYTES)},
    )
    conflict = await harness.client.post(
        "/api/v1/resumes/upload",
        headers=harness.headers(key),
        files={"file": ("different.pdf", PDF_BYTES + b"different")},
    )
    count = await harness.session.scalar(
        select(func.count()).select_from(Resume).where(Resume.id == expected_id)
    )
    assert retry.status_code == 202
    assert retry.json()["data"]["resume_id"] == str(expected_id)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert count == 1
    assert harness.outer_transaction.is_active
    assert harness.dispatcher.calls == [(expected_id, 1), (expected_id, 1)]


async def run_concurrent_uploads(
    *,
    same_bytes: bool,
    tmp_path: Path,
) -> tuple[list[Resume | BaseException], int, bytes, uuid.UUID, uuid.UUID]:
    settings = integration_database_settings()
    engine = create_engine(settings)
    assert engine is not None
    user = make_integration_user("race")
    key = uuid.uuid4()
    resume_id = derive_idempotent_resource_id(user.id, RESUME_UPLOAD_ROUTE, key)
    storage = LocalResumeStorage(tmp_path / "race-storage")
    first_session = AsyncSession(engine, expire_on_commit=False)
    second_session = AsyncSession(engine, expire_on_commit=False)
    setup_session = AsyncSession(engine, expire_on_commit=False)
    cleanup_session = AsyncSession(engine, expire_on_commit=False)
    stored_bytes = b""
    count = -1
    try:
        setup_session.add(user)
        await setup_session.commit()
        second_bytes = PDF_BYTES if same_bytes else PDF_BYTES + b"different"
        results = await asyncio.gather(
            upload_resume(
                first_session,
                current_user=user,
                idempotency_key=key,
                filename="first.pdf",
                data=PDF_BYTES,
                storage=storage,
                dispatcher=RecordingDispatcher(),
            ),
            upload_resume(
                second_session,
                current_user=user,
                idempotency_key=key,
                filename="second.pdf",
                data=second_bytes,
                storage=storage,
                dispatcher=RecordingDispatcher(),
            ),
            return_exceptions=True,
        )
        count = await first_session.scalar(
            select(func.count()).select_from(Resume).where(Resume.id == resume_id)
        )
        assert await second_session.scalar(select(func.count()).select_from(User)) is not None
        stored_bytes = await storage.read_bytes(f"resumes/{resume_id}/source")
        return results, count, stored_bytes, resume_id, user.id
    finally:
        await first_session.close()
        await second_session.close()
        await setup_session.close()
        try:
            await cleanup_session.execute(delete(Resume).where(Resume.id == resume_id))
            await cleanup_session.execute(delete(User).where(User.id == user.id))
            await cleanup_session.commit()
        finally:
            await cleanup_session.close()
            await engine.dispose()


@pytest.mark.asyncio
async def test_real_postgres_same_file_concurrency_converges_without_500(tmp_path: Path) -> None:
    results, count, stored, resume_id, _ = await run_concurrent_uploads(
        same_bytes=True,
        tmp_path=tmp_path,
    )
    assert count == 1
    assert stored == PDF_BYTES
    assert all(isinstance(result, Resume) for result in results)
    assert {result.id for result in results if isinstance(result, Resume)} == {resume_id}


@pytest.mark.asyncio
async def test_real_postgres_different_fingerprint_race_never_overwrites(
    tmp_path: Path,
) -> None:
    results, count, stored, resume_id, _ = await run_concurrent_uploads(
        same_bytes=False,
        tmp_path=tmp_path,
    )
    resumes = [result for result in results if isinstance(result, Resume)]
    conflicts = [result for result in results if isinstance(result, APIError)]
    assert count == 1
    assert len(resumes) == 1 and resumes[0].id == resume_id
    assert len(conflicts) == 1 and conflicts[0].code == "IDEMPOTENCY_KEY_REUSED"
    assert stored in {PDF_BYTES, PDF_BYTES + b"different"}
    assert hashlib.sha256(stored).hexdigest() == resumes[0].create_request_fingerprint
