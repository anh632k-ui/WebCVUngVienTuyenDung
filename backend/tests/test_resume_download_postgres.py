from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.engine_factory import create_engine
from app.core.exceptions import APIError
from app.models.resume import Resume
from app.models.user import User
from app.services.resume_service import PDF_MIME_TYPE, download_resume_source


class TransactionCheckingStorage:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = 0
        self.session: AsyncSession | None = None

    async def read_bytes_bounded(
        self,
        key: str,
        *,
        expected_size: int,
        maximum_size: int,
    ) -> bytes:
        del key, expected_size, maximum_size
        self.calls += 1
        if self.session is not None:
            assert not self.session.in_transaction()
        return self.content


class GatedStorage(TransactionCheckingStorage):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def read_bytes_bounded(
        self,
        key: str,
        *,
        expected_size: int,
        maximum_size: int,
    ) -> bytes:
        result = await super().read_bytes_bounded(
            key,
            expected_size=expected_size,
            maximum_size=maximum_size,
        )
        self.entered.set()
        await self.release.wait()
        return result


class CacheAfterReadStorage(GatedStorage):
    def __init__(self, content: bytes, *, session: AsyncSession, resume_id: uuid.UUID) -> None:
        super().__init__(content)
        self.session = session
        self.resume_id = resume_id
        self.strong_reference: Resume | None = None

    async def read_bytes_bounded(
        self,
        key: str,
        *,
        expected_size: int,
        maximum_size: int,
    ) -> bytes:
        del key, expected_size, maximum_size
        assert self.session is not None and not self.session.in_transaction()
        self.strong_reference = await self.session.get(Resume, self.resume_id)
        assert self.strong_reference is not None
        self.session.expunge(self.strong_reference)
        await self.session.rollback()
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return self.content


@dataclass(frozen=True)
class DownloadHarness:
    session_factory: async_sessionmaker[AsyncSession]
    users: dict[str, uuid.UUID]
    resumes: dict[str, uuid.UUID]
    content: bytes


def make_user(unique: str, name: str, role: str) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=f"download.{unique}.{name}@example.com",
        password_hash="not-used",
        full_name=f"Download {name}",
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


@pytest_asyncio.fixture
async def download_postgres() -> AsyncIterator[DownloadHarness]:
    configured = Settings()
    if configured.database_url is None:
        pytest.skip("DATABASE_URL is not configured")
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=configured.database_url,
        database_connect_timeout_seconds=configured.database_connect_timeout_seconds,
    )
    engine = create_engine(settings)
    assert engine is not None
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 - never expose database credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    unique = uuid.uuid4().hex
    users = {
        "owner": make_user(unique, "owner", "CANDIDATE"),
        "other": make_user(unique, "other", "HR"),
        "admin": make_user(unique, "admin", "ADMIN"),
    }
    content = b"%PDF-1.7\ncanonical download bytes\x00"
    now = datetime.now(UTC)
    resumes: dict[str, Resume] = {}
    for status in ("PENDING", "PROCESSING", "PARSED", "FAILED"):
        resume_id = uuid.uuid4()
        resumes[status] = Resume(
            id=resume_id,
            owner_user_id=users["owner"].id,
            file_name=f"{status.lower()}-ứng-viên.pdf",
            storage_key=f"resumes/{resume_id}/source",
            file_size=len(content),
            mime_type=PDF_MIME_TYPE,
            create_request_fingerprint=hashlib.sha256(content).hexdigest(),
            revision=1,
            parsing_status=status,
            raw_text="parsed" if status == "PARSED" else None,
            resume_embedding=[0.1] * 1024 if status == "PARSED" else None,
            embedding_model="BAAI/bge-m3" if status == "PARSED" else None,
            embedding_preprocessing_version="resume-text-v1" if status == "PARSED" else None,
            error_message="failed" if status == "FAILED" else None,
            is_manually_edited=False,
            is_deleted=False,
            created_at=now,
            updated_at=now,
            parsed_at=now if status == "PARSED" else None,
            deleted_at=None,
        )
    deleted_resume_id = uuid.uuid4()
    resumes["DELETED"] = Resume(
        id=deleted_resume_id,
        owner_user_id=users["owner"].id,
        file_name="deleted.pdf",
        storage_key=f"resumes/{deleted_resume_id}/source",
        file_size=len(content),
        mime_type=PDF_MIME_TYPE,
        create_request_fingerprint=hashlib.sha256(content).hexdigest(),
        revision=1,
        parsing_status="PENDING",
        is_manually_edited=False,
        is_deleted=True,
        created_at=now,
        updated_at=now,
        deleted_at=now,
    )
    async with factory() as setup:
        setup.add_all(users.values())
        await setup.flush()
        setup.add_all(resumes.values())
        await setup.commit()

    harness = DownloadHarness(
        factory,
        {name: user.id for name, user in users.items()},
        {name: resume.id for name, resume in resumes.items()},
        content,
    )
    try:
        yield harness
    finally:
        async with factory() as cleanup:
            await cleanup.execute(delete(User).where(User.id.in_(harness.users.values())))
            await cleanup.commit()
        await engine.dispose()


async def actor(session: AsyncSession, user_id: uuid.UUID) -> User:
    value = await session.get(User, user_id)
    assert value is not None
    return value


@pytest.mark.asyncio
async def test_all_parse_states_are_downloadable_and_read_only(
    download_postgres: DownloadHarness,
) -> None:
    harness = download_postgres
    before: dict[str, tuple[int, str, bool]] = {}
    async with harness.session_factory() as snapshot:
        for status in ("PENDING", "PROCESSING", "PARSED", "FAILED"):
            row = await snapshot.get(Resume, harness.resumes[status])
            assert row is not None
            before[status] = (row.revision, row.parsing_status, row.is_manually_edited)

    for status in ("PENDING", "PROCESSING", "PARSED", "FAILED"):
        async with harness.session_factory() as session:
            storage = TransactionCheckingStorage(harness.content)
            storage.session = session
            result = await download_resume_source(
                session,
                current_user=await actor(session, harness.users["owner"]),
                resume_id=harness.resumes[status],
                storage=storage,  # type: ignore[arg-type]
            )
            assert result.content == harness.content
            assert storage.calls == 1

    async with harness.session_factory() as snapshot:
        for status, expected in before.items():
            row = await snapshot.get(Resume, harness.resumes[status])
            assert row is not None
            assert (row.revision, row.parsing_status, row.is_manually_edited) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["PENDING", "DELETED", "missing"])
async def test_nonowner_deleted_and_missing_are_indistinguishable_without_storage_read(
    download_postgres: DownloadHarness,
    target: str,
) -> None:
    harness = download_postgres
    resume_id = harness.resumes.get(target, uuid.uuid4())
    user_name = "other" if target == "PENDING" else "owner"
    storage = TransactionCheckingStorage(harness.content)
    async with harness.session_factory() as session:
        with pytest.raises(APIError) as captured:
            await download_resume_source(
                session,
                current_user=await actor(session, harness.users[user_name]),
                resume_id=resume_id,
                storage=storage,  # type: ignore[arg-type]
            )
    assert (captured.value.status_code, captured.value.code) == (404, "RESUME_NOT_FOUND")
    assert storage.calls == 0


@pytest.mark.asyncio
async def test_admin_override_can_download_another_users_resume(
    download_postgres: DownloadHarness,
) -> None:
    harness = download_postgres
    async with harness.session_factory() as session:
        result = await download_resume_source(
            session,
            current_user=await actor(session, harness.users["admin"]),
            resume_id=harness.resumes["PENDING"],
            storage=TransactionCheckingStorage(harness.content),  # type: ignore[arg-type]
        )
    assert result.content == harness.content


@pytest.mark.asyncio
async def test_committed_soft_delete_during_storage_read_wins_without_db_lock(
    download_postgres: DownloadHarness,
) -> None:
    harness = download_postgres
    resume_id = harness.resumes["PROCESSING"]
    async with (
        harness.session_factory() as downloader,
        harness.session_factory() as deleter,
    ):
        downloader_pid = await downloader.scalar(text("SELECT pg_backend_pid()"))
        deleter_pid = await deleter.scalar(text("SELECT pg_backend_pid()"))
        await deleter.rollback()
        assert downloader_pid is not None and deleter_pid is not None
        assert downloader_pid != deleter_pid
        storage = GatedStorage(harness.content)
        storage.session = downloader
        task = asyncio.create_task(
            download_resume_source(
                downloader,
                current_user=await actor(downloader, harness.users["owner"]),
                resume_id=resume_id,
                storage=storage,  # type: ignore[arg-type]
            )
        )
        try:
            await asyncio.wait_for(storage.entered.wait(), timeout=5)
            await asyncio.wait_for(
                deleter.execute(
                    update(Resume)
                    .where(Resume.id == resume_id)
                    .values(is_deleted=True, deleted_at=datetime.now(UTC))
                ),
                timeout=5,
            )
            await asyncio.wait_for(deleter.commit(), timeout=5)
            storage.release.set()
            with pytest.raises(APIError) as captured:
                await asyncio.wait_for(task, timeout=5)
        finally:
            storage.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert (captured.value.status_code, captured.value.code) == (404, "RESUME_NOT_FOUND")


@pytest.mark.asyncio
async def test_stale_cached_resume_cannot_bypass_initial_visibility_guard(
    download_postgres: DownloadHarness,
) -> None:
    harness = download_postgres
    resume_id = harness.resumes["PENDING"]
    storage = TransactionCheckingStorage(harness.content)
    async with (
        harness.session_factory() as downloader,
        harness.session_factory() as deleter,
    ):
        cached = await downloader.get(Resume, resume_id)
        assert cached is not None and not cached.is_deleted
        await deleter.execute(
            update(Resume)
            .where(Resume.id == resume_id)
            .values(is_deleted=True, deleted_at=datetime.now(UTC))
        )
        await deleter.commit()
        assert not cached.is_deleted

        with pytest.raises(APIError) as captured:
            await download_resume_source(
                downloader,
                current_user=await actor(downloader, harness.users["owner"]),
                resume_id=resume_id,
                storage=storage,  # type: ignore[arg-type]
            )

    assert (captured.value.status_code, captured.value.code) == (404, "RESUME_NOT_FOUND")
    assert storage.calls == 0


@pytest.mark.asyncio
async def test_final_guard_ignores_stale_identity_created_after_initial_transaction(
    download_postgres: DownloadHarness,
) -> None:
    harness = download_postgres
    resume_id = harness.resumes["FAILED"]
    async with (
        harness.session_factory() as downloader,
        harness.session_factory() as deleter,
    ):
        storage = CacheAfterReadStorage(
            harness.content,
            session=downloader,
            resume_id=resume_id,
        )
        task = asyncio.create_task(
            download_resume_source(
                downloader,
                current_user=await actor(downloader, harness.users["owner"]),
                resume_id=resume_id,
                storage=storage,  # type: ignore[arg-type]
            )
        )
        try:
            await asyncio.wait_for(storage.entered.wait(), timeout=5)
            assert storage.strong_reference is not None
            await deleter.execute(
                update(Resume)
                .where(Resume.id == resume_id)
                .values(is_deleted=True, deleted_at=datetime.now(UTC))
            )
            await deleter.commit()
            assert not storage.strong_reference.is_deleted
            storage.release.set()
            with pytest.raises(APIError) as captured:
                await asyncio.wait_for(task, timeout=5)
        finally:
            storage.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert (captured.value.status_code, captured.value.code) == (404, "RESUME_NOT_FOUND")


@pytest.mark.asyncio
async def test_visibility_read_does_not_autoflush_unrelated_pending_mutation(
    download_postgres: DownloadHarness,
) -> None:
    harness = download_postgres
    original_name = "Download other"
    async with harness.session_factory() as downloader:
        current_user = await actor(downloader, harness.users["owner"])
        unrelated = await downloader.get(User, harness.users["other"])
        assert unrelated is not None and unrelated.full_name == original_name
        unrelated.full_name = "must-not-be-flushed"
        storage = TransactionCheckingStorage(harness.content)
        with pytest.raises(APIError):
            await download_resume_source(
                downloader,
                current_user=current_user,
                resume_id=uuid.uuid4(),
                storage=storage,  # type: ignore[arg-type]
            )
        assert storage.calls == 0

    async with harness.session_factory() as verify:
        persisted_name = await verify.scalar(
            text("SELECT full_name FROM users WHERE id = :id"),
            {"id": harness.users["other"]},
        )
    assert persisted_name == original_name


@pytest.mark.asyncio
async def test_concurrent_revision_update_survives_download_unchanged(
    download_postgres: DownloadHarness,
) -> None:
    harness = download_postgres
    resume_id = harness.resumes["PARSED"]
    async with (
        harness.session_factory() as downloader,
        harness.session_factory() as editor,
    ):
        storage = GatedStorage(harness.content)
        task = asyncio.create_task(
            download_resume_source(
                downloader,
                current_user=await actor(downloader, harness.users["owner"]),
                resume_id=resume_id,
                storage=storage,  # type: ignore[arg-type]
            )
        )
        try:
            await asyncio.wait_for(storage.entered.wait(), timeout=5)
            await editor.execute(
                update(Resume)
                .where(Resume.id == resume_id)
                .values(revision=2, is_manually_edited=True)
            )
            await asyncio.wait_for(editor.commit(), timeout=5)
            storage.release.set()
            result = await asyncio.wait_for(task, timeout=5)
        finally:
            storage.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert result.content == harness.content
    async with harness.session_factory() as verify:
        row = await verify.get(Resume, resume_id)
        assert row is not None
        assert (row.revision, row.is_manually_edited) == (2, True)
