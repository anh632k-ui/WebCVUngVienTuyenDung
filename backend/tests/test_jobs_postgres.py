from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.core.config import Settings, get_settings
from app.core.database import create_engine, get_db_session
from app.core.security import create_access_token
from app.models.job import JobDescription
from app.models.skill import JobSkill, Skill
from app.models.user import User
from main import app

TEST_JWT_SECRET = "job-postgres-integration-jwt-secret"


@dataclass
class PostgreSQLJobHarness:
    client: AsyncClient
    session: AsyncSession
    users: dict[str, User]
    jobs: dict[str, JobDescription]
    settings: Settings
    unique_part: str
    outer_transaction: AsyncTransaction

    def headers(self, user_name: str) -> dict[str, str]:
        token = create_access_token(self.users[user_name].id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def postgres_jobs() -> AsyncIterator[PostgreSQLJobHarness]:
    configured_settings = Settings()
    if configured_settings.database_url is None:
        pytest.skip("DATABASE_URL is not configured")

    database_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=configured_settings.database_url,
        database_connect_timeout_seconds=(configured_settings.database_connect_timeout_seconds),
    )
    integration_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=None,
        jwt_secret_key=TEST_JWT_SECRET,
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
    unique_part = uuid.uuid4().hex
    now = datetime.now(UTC)

    def make_user(name: str, role: str) -> User:
        return User(
            id=uuid.uuid4(),
            email=f"job.integration.{unique_part}.{name}@example.com",
            password_hash="not-used-by-job-integration",
            full_name=f"Job Integration {name}",
            phone_number=None,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    users = {
        "candidate": make_user("candidate", "CANDIDATE"),
        "hr_one": make_user("hr-one", "HR"),
        "hr_two": make_user("hr-two", "HR"),
        "admin": make_user("admin", "ADMIN"),
    }
    session.add_all(users.values())
    await session.commit()

    def make_job(
        name: str,
        recruiter: User,
        status: str,
        *,
        deleted: bool = False,
    ) -> JobDescription:
        active = status == "ACTIVE"
        return JobDescription(
            id=uuid.uuid4(),
            recruiter_id=recruiter.id,
            title=f"{unique_part} {name}",
            job_level="MID",
            location="Hanoi",
            raw_content=f"PostgreSQL Python {name}",
            create_request_fingerprint=uuid.uuid4().hex * 2,
            revision=1,
            min_experience_years=Decimal("2.0"),
            education_requirement="Bachelor",
            job_embedding=[0.0] * 1024 if active else None,
            embedding_model="integration-model" if active else None,
            embedding_preprocessing_version="v1" if active else None,
            parsing_status="PARSED" if active else "PENDING",
            parsing_error_message=None,
            is_criteria_verified=active,
            w_skill=Decimal("0.500"),
            w_semantic=Decimal("0.300"),
            w_experience=Decimal("0.200"),
            status=status,
            is_deleted=deleted,
            created_at=now,
            updated_at=now,
            parsed_at=now if active else None,
            deleted_at=now if deleted else None,
        )

    jobs = {
        "draft": make_job("draft", users["hr_one"], "DRAFT"),
        "active_one": make_job("active-one", users["hr_one"], "ACTIVE"),
        "closed": make_job("closed", users["hr_one"], "CLOSED"),
        "active_two": make_job("active-two", users["hr_two"], "ACTIVE"),
        "deleted": make_job("deleted", users["hr_one"], "ACTIVE", deleted=True),
    }
    session.add_all(jobs.values())
    await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    def override_settings() -> Settings:
        return integration_settings

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    tracked_user_ids = {user.id for user in users.values()}
    tracked_job_ids = {job.id for job in jobs.values()}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield PostgreSQLJobHarness(
                client,
                session,
                users,
                jobs,
                integration_settings,
                unique_part,
                outer_transaction,
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_settings, None)
        remaining_users: int | None = None
        remaining_jobs: int | None = None
        try:
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()
            remaining_users = await connection.scalar(
                select(func.count()).select_from(User).where(User.id.in_(tracked_user_ids))
            )
            remaining_jobs = await connection.scalar(
                select(func.count())
                .select_from(JobDescription)
                .where(JobDescription.id.in_(tracked_job_ids))
            )
        finally:
            if connection.in_transaction():
                await connection.rollback()
            await connection.close()
            await engine.dispose()
        assert remaining_users == 0 and remaining_jobs == 0


@pytest.mark.asyncio
async def test_real_postgres_job_read_endpoints(postgres_jobs: PostgreSQLJobHarness) -> None:
    harness = postgres_jobs
    initial_state = {job.id: (job.status, job.updated_at) for job in harness.jobs.values()}

    candidate_listing = await harness.client.get(
        "/api/v1/jobs",
        params={"keyword": harness.unique_part.upper(), "limit": 1, "page": 2},
        headers=harness.headers("candidate"),
    )
    assert candidate_listing.status_code == 200
    assert candidate_listing.json()["meta"] == {
        "page": 2,
        "limit": 1,
        "total_items": 2,
        "total_pages": 2,
    }
    assert candidate_listing.json()["data"][0]["status"] == "ACTIVE"

    for name in ("draft", "closed", "deleted"):
        response = await harness.client.get(
            f"/api/v1/jobs/{harness.jobs[name].id}",
            headers=harness.headers("candidate"),
        )
        assert response.status_code == 404

    active_detail = await harness.client.get(
        f"/api/v1/jobs/{harness.jobs['active_one'].id}",
        headers=harness.headers("candidate"),
    )
    assert active_detail.status_code == 200
    assert active_detail.json()["data"]["min_experience_years"] == 2.0
    assert "job_embedding" not in active_detail.text
    assert "create_request_fingerprint" not in active_detail.text

    hr_listing = await harness.client.get(
        "/api/v1/jobs",
        params={"keyword": "POSTGRESQL", "status": "DRAFT", "parsing_status": "PENDING"},
        headers=harness.headers("hr_one"),
    )
    hr_denied = await harness.client.get(
        f"/api/v1/jobs/{harness.jobs['active_two'].id}",
        headers=harness.headers("hr_one"),
    )
    assert hr_listing.status_code == 200
    assert hr_listing.json()["meta"]["total_items"] == 1
    assert hr_listing.json()["data"][0]["id"] == str(harness.jobs["draft"].id)
    assert hr_denied.status_code == 404

    admin_listing = await harness.client.get(
        "/api/v1/jobs",
        params={"keyword": harness.unique_part, "limit": 100},
        headers=harness.headers("admin"),
    )
    admin_detail = await harness.client.get(
        f"/api/v1/jobs/{harness.jobs['closed'].id}",
        headers=harness.headers("admin"),
    )
    assert admin_listing.json()["meta"]["total_items"] == 4
    assert admin_detail.status_code == 200

    harness.session.expire_all()
    persisted = list(
        (
            await harness.session.scalars(
                select(JobDescription).where(JobDescription.id.in_(initial_state))
            )
        ).all()
    )
    assert {job.id: (job.status, job.updated_at) for job in persisted} == initial_state
    assert harness.outer_transaction.is_active


@pytest.mark.asyncio
async def test_real_postgres_job_soft_delete(
    postgres_jobs: PostgreSQLJobHarness,
) -> None:
    harness = postgres_jobs
    target = harness.jobs["active_one"]
    target_id = target.id
    hr_headers = harness.headers("hr_one")
    original = (
        target.revision,
        target.raw_content,
        target.parsing_status,
        target.is_criteria_verified,
        target.updated_at,
    )

    response = await harness.client.delete(f"/api/v1/jobs/{target_id}", headers=hr_headers)
    assert response.status_code == 204 and response.content == b""

    harness.session.expire_all()
    persisted = await harness.session.scalar(
        select(JobDescription).where(JobDescription.id == target_id)
    )
    assert persisted is not None
    assert persisted.is_deleted is True
    assert persisted.deleted_at is not None
    assert persisted.updated_at >= original[4]
    assert (
        persisted.revision,
        persisted.raw_content,
        persisted.parsing_status,
        persisted.is_criteria_verified,
    ) == original[:4]

    listing = await harness.client.get("/api/v1/jobs", headers=hr_headers)
    detail = await harness.client.get(f"/api/v1/jobs/{target_id}", headers=hr_headers)
    assert str(target_id) not in listing.text
    assert detail.status_code == 404
    assert harness.outer_transaction.is_active


@pytest.mark.asyncio
async def test_real_postgres_job_status_transitions(
    postgres_jobs: PostgreSQLJobHarness,
) -> None:
    harness = postgres_jobs
    target = harness.jobs["draft"]
    target_id = target.id
    headers = harness.headers("hr_one")
    original_revision = target.revision

    invalid = await harness.client.patch(
        f"/api/v1/jobs/{target_id}/status",
        json={"status": "CLOSED"},
        headers=headers,
    )
    not_ready = await harness.client.patch(
        f"/api/v1/jobs/{target_id}/status",
        json={"status": "ACTIVE"},
        headers=headers,
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"
    assert not_ready.status_code == 422
    assert not_ready.json()["error"]["code"] == "JOB_NOT_READY"

    skill_id = await harness.session.scalar(select(Skill.id).order_by(Skill.id).limit(1))
    assert skill_id is not None
    target.parsing_status = "PARSED"
    target.is_criteria_verified = True
    target.job_embedding = [0.0] * 1024
    target.embedding_model = "integration-model"
    target.embedding_preprocessing_version = "v1"
    target.parsed_at = datetime.now(UTC)
    harness.session.add(
        JobSkill(
            job_id=target_id,
            skill_id=skill_id,
            importance="MANDATORY",
            min_years_required=Decimal("1.0"),
        )
    )
    await harness.session.commit()

    for expected_status in ("ACTIVE", "CLOSED", "ACTIVE", "DRAFT"):
        response = await harness.client.patch(
            f"/api/v1/jobs/{target_id}/status",
            json={"status": expected_status},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["status"] == expected_status
        assert response.json()["data"]["revision"] == original_revision

    persisted = await harness.session.scalar(
        select(JobDescription).where(JobDescription.id == target_id)
    )
    assert persisted is not None
    same_state_updated_at = persisted.updated_at
    idempotent = await harness.client.patch(
        f"/api/v1/jobs/{target_id}/status",
        json={"status": "DRAFT"},
        headers=headers,
    )
    assert idempotent.status_code == 200
    await harness.session.refresh(persisted)
    assert persisted.status == "DRAFT"
    assert persisted.updated_at == same_state_updated_at
    assert persisted.revision == original_revision
    assert harness.outer_transaction.is_active
