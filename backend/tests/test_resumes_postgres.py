from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.core.config import Settings, get_settings
from app.core.database import create_engine, get_db_session
from app.core.security import create_access_token
from app.models.resume import CandidateProfile, Resume, ResumeEducation, ResumeExperience
from app.models.skill import ResumeSkill, Skill
from app.models.user import User
from main import app

TEST_JWT_SECRET = "resume-postgres-integration-jwt-secret"


@dataclass
class PostgreSQLResumeHarness:
    client: AsyncClient
    session: AsyncSession
    users: dict[str, User]
    resumes: dict[str, Resume]
    settings: Settings
    unique_part: str
    outer_transaction: AsyncTransaction

    def headers(self, user_name: str) -> dict[str, str]:
        token = create_access_token(self.users[user_name].id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def postgres_resumes() -> AsyncIterator[PostgreSQLResumeHarness]:
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
            email=f"resume.integration.{unique_part}.{name}@example.com",
            password_hash="not-used-by-resume-integration",
            full_name=f"Resume Integration {name}",
            phone_number=None,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    users = {
        "candidate": make_user("candidate", "CANDIDATE"),
        "other": make_user("other", "CANDIDATE"),
        "hr": make_user("hr", "HR"),
        "admin": make_user("admin", "ADMIN"),
    }
    session.add_all(users.values())
    await session.commit()

    def make_resume(
        name: str,
        owner: User,
        status: str,
        *,
        deleted: bool = False,
    ) -> Resume:
        is_parsed = status == "PARSED"
        return Resume(
            id=uuid.uuid4(),
            owner_user_id=owner.id,
            file_name=f"{unique_part}-{name}.pdf",
            storage_key=f"resume-integration/{unique_part}/{name}.pdf",
            file_size=100,
            mime_type="application/pdf",
            create_request_fingerprint=uuid.uuid4().hex * 2,
            revision=1,
            parsing_status=status,
            raw_text="parsed text" if is_parsed else None,
            resume_embedding=[0.0] * 1024 if is_parsed else None,
            embedding_model="integration-model" if is_parsed else None,
            embedding_preprocessing_version="v1" if is_parsed else None,
            error_message="integration failure" if status == "FAILED" else None,
            is_manually_edited=False,
            is_deleted=deleted,
            created_at=now,
            updated_at=now,
            parsed_at=now if is_parsed else None,
            deleted_at=now if deleted else None,
        )

    resumes = {
        "pending": make_resume("pending", users["candidate"], "PENDING"),
        "processing": make_resume("processing", users["candidate"], "PROCESSING"),
        "failed": make_resume("failed", users["candidate"], "FAILED"),
        "parsed": make_resume("parsed", users["candidate"], "PARSED"),
        "deleted": make_resume("deleted", users["candidate"], "PENDING", deleted=True),
        "other": make_resume("other", users["other"], "PENDING"),
        "hr": make_resume("hr", users["hr"], "PENDING"),
    }
    session.add_all(resumes.values())
    await session.commit()

    skill_id = await session.scalar(select(Skill.id).order_by(Skill.id.asc()).limit(1))
    assert skill_id is not None, "Canonical skill taxonomy is empty"
    parsed_id = resumes["parsed"].id
    session.add_all(
        [
            CandidateProfile(
                resume_id=parsed_id,
                full_name="PostgreSQL Parsed Candidate",
                email="parsed@example.com",
                current_title="Engineer",
            ),
            ResumeSkill(
                resume_id=parsed_id,
                skill_id=skill_id,
                years_of_experience=Decimal("2.0"),
                proficiency_level="ADVANCED",
            ),
            ResumeExperience(
                resume_id=parsed_id,
                company_name="Integration Company",
                job_title="Engineer",
                start_date=date(2023, 1, 1),
                is_current=True,
            ),
            ResumeEducation(
                resume_id=parsed_id,
                institution_name="Integration University",
                degree="BSc",
                start_year=2019,
                graduation_year=2023,
            ),
        ]
    )
    await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    def override_settings() -> Settings:
        return integration_settings

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    tracked_user_ids = {item.id for item in users.values()}
    tracked_resume_ids = {item.id for item in resumes.values()}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield PostgreSQLResumeHarness(
                client,
                session,
                users,
                resumes,
                integration_settings,
                unique_part,
                outer_transaction,
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_settings, None)
        remaining_users: int | None = None
        remaining_resumes: int | None = None
        try:
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()
            remaining_users = await connection.scalar(
                select(func.count()).select_from(User).where(User.id.in_(tracked_user_ids))
            )
            remaining_resumes = await connection.scalar(
                select(func.count()).select_from(Resume).where(Resume.id.in_(tracked_resume_ids))
            )
        finally:
            if connection.in_transaction():
                await connection.rollback()
            await connection.close()
            await engine.dispose()
        assert remaining_users == 0 and remaining_resumes == 0


@pytest.mark.asyncio
async def test_real_postgres_resume_read_endpoints(
    postgres_resumes: PostgreSQLResumeHarness,
) -> None:
    harness = postgres_resumes
    initial_state = {
        item.id: (item.parsing_status, item.updated_at) for item in harness.resumes.values()
    }
    candidate_headers = harness.headers("candidate")

    listing = await harness.client.get(
        "/api/v1/resumes",
        params={"keyword": harness.unique_part, "page": 2, "limit": 2},
        headers=candidate_headers,
    )
    assert listing.status_code == 200
    assert listing.json()["meta"] == {
        "page": 2,
        "limit": 2,
        "total_items": 4,
        "total_pages": 2,
    }
    assert len(listing.json()["data"]) == 2
    assert "storage_key" not in listing.text

    failed = await harness.client.get(
        "/api/v1/resumes",
        params={"keyword": harness.unique_part.upper(), "parsing_status": "FAILED"},
        headers=candidate_headers,
    )
    assert failed.status_code == 200
    assert failed.json()["meta"]["total_items"] == 1
    assert failed.json()["data"][0]["parsing_status"] == "FAILED"

    unauthorized = await harness.client.get(
        f"/api/v1/resumes/{harness.resumes['other'].id}", headers=candidate_headers
    )
    deleted = await harness.client.get(
        f"/api/v1/resumes/{harness.resumes['deleted'].id}", headers=candidate_headers
    )
    assert unauthorized.status_code == 404
    assert deleted.status_code == 404

    pending = await harness.client.get(
        f"/api/v1/resumes/{harness.resumes['pending'].id}", headers=candidate_headers
    )
    parsed = await harness.client.get(
        f"/api/v1/resumes/{harness.resumes['parsed'].id}", headers=candidate_headers
    )
    assert pending.status_code == 200
    assert pending.json()["data"]["candidate_profile"] is None
    assert parsed.status_code == 200
    assert parsed.json()["data"]["candidate_profile"]["full_name"] == (
        "PostgreSQL Parsed Candidate"
    )
    assert parsed.json()["data"]["skills"][0]["years_of_experience"] == 2.0
    assert parsed.json()["data"]["experiences"][0]["company_name"] == ("Integration Company")
    assert parsed.json()["data"]["educations"][0]["institution_name"] == ("Integration University")

    for name, status in (
        ("pending", "PENDING"),
        ("processing", "PROCESSING"),
        ("parsed", "PARSED"),
        ("failed", "FAILED"),
    ):
        response = await harness.client.get(
            f"/api/v1/resumes/{harness.resumes[name].id}/status",
            headers=candidate_headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["parsing_status"] == status

    hr_listing = await harness.client.get("/api/v1/resumes", headers=harness.headers("hr"))
    admin_listing = await harness.client.get(
        "/api/v1/resumes",
        params={"keyword": harness.unique_part, "limit": 100},
        headers=harness.headers("admin"),
    )
    assert hr_listing.json()["meta"]["total_items"] == 1
    assert admin_listing.json()["meta"]["total_items"] == 6

    harness.session.expire_all()
    persisted = list(
        (await harness.session.scalars(select(Resume).where(Resume.id.in_(initial_state)))).all()
    )
    assert {item.id: (item.parsing_status, item.updated_at) for item in persisted} == initial_state
    assert harness.outer_transaction.is_active
