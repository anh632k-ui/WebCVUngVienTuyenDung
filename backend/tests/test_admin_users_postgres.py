from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.core.config import Settings, get_settings
from app.core.database import create_engine, get_db_session
from app.core.security import create_access_token
from app.models.job import JobDescription
from app.models.resume import Resume
from app.models.user import User
from main import app

TEST_JWT_SECRET = "admin-postgres-integration-jwt-secret"


@dataclass
class PostgreSQLAdminHarness:
    client: AsyncClient
    session: AsyncSession
    admin: User
    users: dict[str, User]
    settings: Settings
    outer_transaction: AsyncTransaction

    @property
    def headers(self) -> dict[str, str]:
        token = create_access_token(self.admin.id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def postgres_admin() -> AsyncIterator[PostgreSQLAdminHarness]:
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
            email=f"admin.integration.{unique_part}.{name}@example.com",
            password_hash="not-used-by-admin-integration",
            full_name=f"Admin Integration {unique_part} {name}",
            phone_number=None,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    users = {
        "admin": make_user("admin", "ADMIN"),
        "other_admin": make_user("other-admin", "ADMIN"),
        "free_candidate": make_user("free-candidate", "CANDIDATE"),
        "resume_candidate": make_user("resume-candidate", "CANDIDATE"),
        "job_hr": make_user("job-hr", "HR"),
    }
    session.add_all(users.values())
    await session.commit()

    resume = Resume(
        owner_user_id=users["resume_candidate"].id,
        file_name="admin-integration.pdf",
        storage_key=f"admin-integration/{unique_part}.pdf",
        file_size=1,
        mime_type="application/pdf",
        create_request_fingerprint="a" * 64,
    )
    job = JobDescription(
        recruiter_id=users["job_hr"].id,
        title="Admin integration job",
        job_level="MID",
        raw_content="Admin integration test content",
        create_request_fingerprint="b" * 64,
    )
    session.add_all([resume, job])
    await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    def override_settings() -> Settings:
        return integration_settings

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    harness: PostgreSQLAdminHarness | None = None
    tracked_ids = {user.id for user in users.values()}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            harness = PostgreSQLAdminHarness(
                client=client,
                session=session,
                admin=users["admin"],
                users=users,
                settings=integration_settings,
                outer_transaction=outer_transaction,
            )
            yield harness
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_settings, None)
        remaining_users: int | None = None
        try:
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()
            remaining_users = await connection.scalar(
                select(func.count()).select_from(User).where(User.id.in_(tracked_ids))
            )
        finally:
            if connection.in_transaction():
                await connection.rollback()
            await connection.close()
            await engine.dispose()

        assert remaining_users == 0, "Outer rollback left admin integration rows behind"


@pytest.mark.asyncio
async def test_real_postgres_admin_user_management(
    postgres_admin: PostgreSQLAdminHarness,
) -> None:
    harness = postgres_admin
    keyword = harness.users["admin"].full_name.split()[2]
    listing = await harness.client.get(
        "/api/v1/admin/users",
        params={"keyword": keyword, "page": 1, "limit": 2},
        headers=harness.headers,
    )
    assert listing.status_code == 200
    assert listing.json()["meta"] == {
        "page": 1,
        "limit": 2,
        "total_items": 5,
        "total_pages": 3,
    }
    assert len(listing.json()["data"]) == 2
    assert "password_hash" not in listing.text

    candidate_listing = await harness.client.get(
        "/api/v1/admin/users",
        params={"keyword": keyword, "role": "CANDIDATE", "is_active": True},
        headers=harness.headers,
    )
    assert candidate_listing.status_code == 200
    assert candidate_listing.json()["meta"]["total_items"] == 2
    assert {item["role"] for item in candidate_listing.json()["data"]} == {"CANDIDATE"}

    self_lock = await harness.client.patch(
        f"/api/v1/admin/users/{harness.admin.id}",
        json={"is_active": False},
        headers=harness.headers,
    )
    self_demote = await harness.client.patch(
        f"/api/v1/admin/users/{harness.admin.id}",
        json={"role": "HR"},
        headers=harness.headers,
    )
    assert self_lock.status_code == 400
    assert self_lock.json()["error"]["code"] == "ADMIN_SELF_LOCK_FORBIDDEN"
    assert self_demote.status_code == 400
    assert self_demote.json()["error"]["code"] == "ADMIN_SELF_DEMOTION_FORBIDDEN"

    candidate_to_admin = await harness.client.patch(
        f"/api/v1/admin/users/{harness.users['resume_candidate'].id}",
        json={"role": "ADMIN"},
        headers=harness.headers,
    )
    hr_to_admin = await harness.client.patch(
        f"/api/v1/admin/users/{harness.users['job_hr'].id}",
        json={"role": "ADMIN"},
        headers=harness.headers,
    )
    assert candidate_to_admin.status_code == 422
    assert hr_to_admin.status_code == 422

    other_admin = harness.users["other_admin"]
    for role in ("CANDIDATE", "HR"):
        forbidden = await harness.client.patch(
            f"/api/v1/admin/users/{other_admin.id}",
            json={"role": role},
            headers=harness.headers,
        )
        assert forbidden.status_code == 400
        assert forbidden.json()["error"]["code"] == "ADMIN_ROLE_CHANGE_FORBIDDEN"
    deactivate_other_admin = await harness.client.patch(
        f"/api/v1/admin/users/{other_admin.id}",
        json={"is_active": False},
        headers=harness.headers,
    )
    assert deactivate_other_admin.status_code == 200
    assert deactivate_other_admin.json()["data"]["is_active"] is False

    free_candidate = harness.users["free_candidate"]
    deactivate = await harness.client.patch(
        f"/api/v1/admin/users/{free_candidate.id}",
        json={"is_active": False},
        headers=harness.headers,
    )
    promote = await harness.client.patch(
        f"/api/v1/admin/users/{free_candidate.id}",
        json={"role": "HR"},
        headers=harness.headers,
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["data"]["is_active"] is False
    assert promote.status_code == 200
    assert promote.json()["data"]["role"] == "HR"

    resume_candidate = harness.users["resume_candidate"]
    resume_conflict = await harness.client.patch(
        f"/api/v1/admin/users/{resume_candidate.id}",
        json={"role": "HR"},
        headers=harness.headers,
    )
    assert resume_conflict.status_code == 409
    assert resume_conflict.json()["error"]["code"] == "ROLE_CHANGE_CONFLICT"

    resume = await harness.session.scalar(
        select(Resume).where(Resume.owner_user_id == resume_candidate.id)
    )
    assert resume is not None
    resume.is_deleted = True
    resume.deleted_at = datetime.now(UTC)
    await harness.session.commit()
    resume_soft_deleted = await harness.client.patch(
        f"/api/v1/admin/users/{resume_candidate.id}",
        json={"role": "HR"},
        headers=harness.headers,
    )
    assert resume_soft_deleted.status_code == 200

    job_hr = harness.users["job_hr"]
    job_conflict = await harness.client.patch(
        f"/api/v1/admin/users/{job_hr.id}",
        json={"role": "CANDIDATE"},
        headers=harness.headers,
    )
    assert job_conflict.status_code == 409
    assert job_conflict.json()["error"]["code"] == "ROLE_CHANGE_CONFLICT"

    job = await harness.session.scalar(
        select(JobDescription).where(JobDescription.recruiter_id == job_hr.id)
    )
    assert job is not None
    job.is_deleted = True
    job.deleted_at = datetime.now(UTC)
    await harness.session.commit()
    job_soft_deleted = await harness.client.patch(
        f"/api/v1/admin/users/{job_hr.id}",
        json={"role": "CANDIDATE"},
        headers=harness.headers,
    )
    assert job_soft_deleted.status_code == 200
    assert harness.outer_transaction.is_active
