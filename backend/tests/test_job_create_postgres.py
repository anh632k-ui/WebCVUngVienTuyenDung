from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, AsyncTransaction

from app.core.config import Settings, get_settings
from app.core.database import create_engine, get_db_session
from app.core.exceptions import APIError
from app.core.idempotency import JOB_CREATE_ROUTE, derive_idempotent_resource_id
from app.core.security import create_access_token
from app.models.job import JobDescription
from app.models.skill import JobSkill
from app.models.user import User
from app.schemas.job_schema import JobCreateRequest
from app.services.job_create_payload import job_create_fingerprint
from app.services.job_dispatcher import get_job_parse_dispatcher
from app.services.job_service import create_job
from main import app

TEST_SECRET = "job-create-postgres-jwt-secret-value"


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, int]] = []

    async def dispatch(self, job_id: uuid.UUID, revision: int) -> None:
        self.calls.append((job_id, revision))


@dataclass
class PostgreSQLJobCreateHarness:
    client: AsyncClient
    session: AsyncSession
    users: dict[str, User]
    settings: Settings
    dispatcher: RecordingDispatcher
    transaction: AsyncTransaction
    tracked_job_ids: set[uuid.UUID]

    def headers(self, user_name: str, key: uuid.UUID | None = None) -> dict[str, str]:
        token = create_access_token(self.users[user_name].id, self.settings)
        headers = {"Authorization": f"Bearer {token}"}
        if key is not None:
            headers["Idempotency-Key"] = str(key)
        return headers


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


def make_user(label: str, role: str = "HR") -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=f"job.create.{label}.{uuid.uuid4().hex}@example.com",
        password_hash="not-used-by-job-create-integration",
        full_name=f"Job Create {label}",
        phone_number=None,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


@pytest_asyncio.fixture
async def postgres_job_create() -> AsyncIterator[PostgreSQLJobCreateHarness]:
    engine = create_engine(integration_database_settings())
    assert engine is not None
    try:
        connection = await engine.connect()
    except Exception:  # noqa: BLE001 - never expose configured database credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    users = {
        "hr": make_user("hr"),
        "candidate": make_user("candidate", "CANDIDATE"),
        "admin": make_user("admin", "ADMIN"),
    }
    session.add_all(users.values())
    await session.commit()
    integration_settings = Settings(
        _env_file=None,
        app_env="test",
        jwt_secret_key=TEST_SECRET,
    )
    dispatcher = RecordingDispatcher()
    tracked_job_ids: set[uuid.UUID] = set()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    def override_settings() -> Settings:
        return integration_settings

    def override_dispatcher() -> RecordingDispatcher:
        return dispatcher

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_job_parse_dispatcher] = override_dispatcher
    user_ids = {user.id for user in users.values()}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield PostgreSQLJobCreateHarness(
                client,
                session,
                users,
                integration_settings,
                dispatcher,
                transaction,
                tracked_job_ids,
            )
    finally:
        app.dependency_overrides.clear()
        await session.close()
        if transaction.is_active:
            await transaction.rollback()
        remaining_users = await connection.scalar(
            select(func.count()).select_from(User).where(User.id.in_(user_ids))
        )
        remaining_jobs = (
            await connection.scalar(
                select(func.count())
                .select_from(JobDescription)
                .where(JobDescription.id.in_(tracked_job_ids))
            )
            if tracked_job_ids
            else 0
        )
        if connection.in_transaction():
            await connection.rollback()
        await connection.close()
        await engine.dispose()
        assert remaining_users == 0 and remaining_jobs == 0


@pytest.mark.asyncio
async def test_real_postgres_canonical_create_idempotency_states_and_visibility(
    postgres_job_create: PostgreSQLJobCreateHarness,
) -> None:
    harness = postgres_job_create
    key = uuid.uuid4()
    expected_id = derive_idempotent_resource_id(
        harness.users["hr"].id,
        JOB_CREATE_ROUTE,
        key,
    )
    second_key = uuid.uuid4()
    second_id = derive_idempotent_resource_id(
        harness.users["hr"].id,
        JOB_CREATE_ROUTE,
        second_key,
    )
    harness.tracked_job_ids.update({expected_id, second_id})
    request = {
        "title": " Platform Cafe\u0301 Engineer ",
        "job_level": " Senior ",
        "location": "   ",
        "raw_content": "Build services\r\nOperate services",
    }
    first = await harness.client.post(
        "/api/v1/jobs",
        headers=harness.headers("hr", key),
        json=request,
    )
    retry = await harness.client.post(
        "/api/v1/jobs",
        headers=harness.headers("hr", key),
        json={
            "title": "Platform Café Engineer",
            "job_level": "Senior",
            "location": None,
            "raw_content": "Build services\nOperate services",
            "w_skill": 0.500,
            "w_semantic": 0.300,
            "w_experience": 0.200,
        },
    )
    conflict = await harness.client.post(
        "/api/v1/jobs",
        headers=harness.headers("hr", key),
        json=request | {"title": "Different role"},
    )
    different_key = await harness.client.post(
        "/api/v1/jobs",
        headers=harness.headers("hr", second_key),
        json=request,
    )

    assert first.status_code == retry.status_code == different_key.status_code == 201
    assert first.json() == retry.json()
    assert first.json()["data"]["id"] == str(expected_id)
    assert different_key.json()["data"]["id"] == str(second_id)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    row = await harness.session.get(JobDescription, expected_id, populate_existing=True)
    assert row is not None
    assert row.recruiter_id == harness.users["hr"].id
    assert row.title == "Platform Café Engineer"
    assert row.job_level == "Senior"
    assert row.location is None
    assert row.raw_content == "Build services\nOperate services"
    assert row.create_request_fingerprint == job_create_fingerprint(JobCreateRequest(**request))
    assert row.revision == 1
    assert row.min_experience_years == Decimal("0.0")
    assert row.education_requirement is None
    assert row.job_embedding is None
    assert row.embedding_model is None
    assert row.embedding_preprocessing_version is None
    assert row.parsing_status == "PENDING"
    assert row.parsing_error_message is None
    assert row.is_criteria_verified is False
    assert (row.w_skill, row.w_semantic, row.w_experience) == (
        Decimal("0.500"),
        Decimal("0.300"),
        Decimal("0.200"),
    )
    assert row.status == "DRAFT"
    assert row.is_deleted is False
    assert row.deleted_at is None and row.parsed_at is None
    assert (
        await harness.session.scalar(
            select(func.count()).select_from(JobSkill).where(JobSkill.job_id == expected_id)
        )
        == 0
    )
    assert (
        await harness.session.scalar(
            select(func.count()).select_from(JobDescription).where(JobDescription.id == expected_id)
        )
        == 1
    )
    assert harness.dispatcher.calls == [(expected_id, 1), (expected_id, 1), (second_id, 1)]

    owner_read = await harness.client.get(
        f"/api/v1/jobs/{expected_id}", headers=harness.headers("hr")
    )
    candidate_read = await harness.client.get(
        f"/api/v1/jobs/{expected_id}", headers=harness.headers("candidate")
    )
    admin_read = await harness.client.get(
        f"/api/v1/jobs/{expected_id}", headers=harness.headers("admin")
    )
    assert owner_read.status_code == admin_read.status_code == 200
    assert candidate_read.status_code == 404
    assert harness.transaction.is_active


async def run_concurrent_job_creates(
    *,
    same_payload: bool,
) -> tuple[list[JobDescription | BaseException], int, uuid.UUID]:
    engine = create_engine(integration_database_settings())
    assert engine is not None
    user = make_user("race")
    key = uuid.uuid4()
    job_id = derive_idempotent_resource_id(user.id, JOB_CREATE_ROUTE, key)
    first_session = AsyncSession(engine, expire_on_commit=False)
    second_session = AsyncSession(engine, expire_on_commit=False)
    setup_session = AsyncSession(engine, expire_on_commit=False)
    cleanup_session = AsyncSession(engine, expire_on_commit=False)
    try:
        setup_session.add(user)
        await setup_session.commit()
        first_payload = JobCreateRequest(
            title="Concurrency Engineer",
            job_level="Senior",
            raw_content="Build concurrent services",
        )
        second_payload = (
            first_payload
            if same_payload
            else JobCreateRequest(
                title="Different Concurrency Engineer",
                job_level="Senior",
                raw_content="Build concurrent services",
            )
        )
        results = await asyncio.gather(
            create_job(
                first_session,
                current_user=user,
                idempotency_key=key,
                payload=first_payload,
                dispatcher=RecordingDispatcher(),
            ),
            create_job(
                second_session,
                current_user=user,
                idempotency_key=key,
                payload=second_payload,
                dispatcher=RecordingDispatcher(),
            ),
            return_exceptions=True,
        )
        count = await first_session.scalar(
            select(func.count()).select_from(JobDescription).where(JobDescription.id == job_id)
        )
        assert (
            await second_session.scalar(
                select(func.count()).select_from(User).where(User.id == user.id)
            )
            == 1
        )
        return results, count or 0, job_id
    finally:
        await first_session.close()
        await second_session.close()
        await setup_session.close()
        try:
            await cleanup_session.execute(delete(JobDescription).where(JobDescription.id == job_id))
            await cleanup_session.execute(delete(User).where(User.id == user.id))
            await cleanup_session.commit()
        finally:
            await cleanup_session.close()
            await engine.dispose()


@pytest.mark.asyncio
async def test_real_postgres_same_payload_concurrency_converges() -> None:
    results, count, job_id = await run_concurrent_job_creates(same_payload=True)

    assert count == 1
    assert all(isinstance(result, JobDescription) for result in results)
    assert {result.id for result in results if isinstance(result, JobDescription)} == {job_id}


@pytest.mark.asyncio
async def test_real_postgres_different_payload_concurrency_returns_one_conflict() -> None:
    results, count, job_id = await run_concurrent_job_creates(same_payload=False)
    jobs = [result for result in results if isinstance(result, JobDescription)]
    conflicts = [result for result in results if isinstance(result, APIError)]

    assert count == 1
    assert len(jobs) == 1 and jobs[0].id == job_id
    assert len(conflicts) == 1 and conflicts[0].code == "IDEMPOTENCY_KEY_REUSED"
