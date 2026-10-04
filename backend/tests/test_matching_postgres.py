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
from app.models.match_result import MatchResult
from app.models.resume import Resume
from app.models.user import User
from main import app

TEST_JWT_SECRET = "matching-postgres-integration-jwt-secret"


@dataclass
class PostgreSQLMatchingHarness:
    client: AsyncClient
    session: AsyncSession
    users: dict[str, User]
    resumes: dict[str, Resume]
    jobs: dict[str, JobDescription]
    matches: dict[str, MatchResult]
    settings: Settings
    outer_transaction: AsyncTransaction

    def headers(self, name: str) -> dict[str, str]:
        token = create_access_token(self.users[name].id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def postgres_matching() -> AsyncIterator[PostgreSQLMatchingHarness]:
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
    unique = uuid.uuid4().hex
    now = datetime.now(UTC)

    def make_user(name: str, role: str) -> User:
        return User(
            id=uuid.uuid4(),
            email=f"matching.integration.{unique}.{name}@example.com",
            password_hash="not-used-by-matching-integration",
            full_name=f"Matching Integration {name}",
            phone_number=None,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    users = {
        "candidate": make_user("candidate", "CANDIDATE"),
        "other": make_user("other", "CANDIDATE"),
        "hr_one": make_user("hr-one", "HR"),
        "hr_two": make_user("hr-two", "HR"),
        "admin": make_user("admin", "ADMIN"),
    }
    session.add_all(users.values())
    await session.commit()

    def make_resume(name: str, owner: User, *, deleted: bool = False) -> Resume:
        return Resume(
            id=uuid.uuid4(),
            owner_user_id=owner.id,
            file_name=f"{unique}-{name}.pdf",
            storage_key=f"matching-integration/{unique}/{name}.pdf",
            file_size=100,
            mime_type="application/pdf",
            create_request_fingerprint=uuid.uuid4().hex * 2,
            revision=1,
            parsing_status="PENDING",
            is_manually_edited=False,
            is_deleted=deleted,
            created_at=now,
            updated_at=now,
            deleted_at=now if deleted else None,
        )

    resumes = {
        "candidate": make_resume("candidate", users["candidate"]),
        "other": make_resume("other", users["other"]),
        "hr_one": make_resume("hr-one", users["hr_one"]),
        "hr_two": make_resume("hr-two", users["hr_two"]),
        "deleted": make_resume("deleted", users["candidate"], deleted=True),
    }
    session.add_all(resumes.values())
    await session.commit()

    def make_job(name: str, recruiter: User, *, deleted: bool = False) -> JobDescription:
        return JobDescription(
            id=uuid.uuid4(),
            recruiter_id=recruiter.id,
            title=f"{unique} {name}",
            job_level="MID",
            raw_content=f"Matching integration {name}",
            create_request_fingerprint=uuid.uuid4().hex * 2,
            revision=1,
            parsing_status="PENDING",
            is_criteria_verified=False,
            w_skill=Decimal("0.500"),
            w_semantic=Decimal("0.300"),
            w_experience=Decimal("0.200"),
            status="DRAFT",
            is_deleted=deleted,
            created_at=now,
            updated_at=now,
            deleted_at=now if deleted else None,
        )

    jobs = {
        "hr_one": make_job("hr-one", users["hr_one"]),
        "hr_two": make_job("hr-two", users["hr_two"]),
        "deleted": make_job("deleted", users["hr_one"], deleted=True),
    }
    session.add_all(jobs.values())
    await session.commit()

    def make_match(name: str, resume: Resume, job: JobDescription, status: str) -> MatchResult:
        completed = status == "COMPLETED"
        return MatchResult(
            id=uuid.uuid4(),
            job_id=job.id,
            resume_id=resume.id,
            generation=1,
            resume_revision=resume.revision,
            job_revision=job.revision,
            overall_score=Decimal("80.00") if completed else None,
            skill_score=Decimal("75.00") if completed else None,
            semantic_score=Decimal("85.00") if completed else None,
            experience_score=Decimal("80.00") if completed else None,
            matched_skills=[{"skill_id": 1, "name": "Python", "source": name}] if completed else [],
            missing_skills=[{"skill_id": 2, "name": "SQL"}] if completed else [],
            gap_analysis_summary="Improve SQL" if completed else None,
            algorithm_version="hybrid-v1",
            embedding_model="integration-model" if completed else None,
            embedding_preprocessing_version="v1" if completed else None,
            status=status,
            error_message="integration failure" if status == "FAILED" else None,
            created_at=now,
            updated_at=now,
            calculated_at=now if completed else None,
        )

    matches = {
        "candidate_completed": make_match(
            "candidate-completed", resumes["candidate"], jobs["hr_one"], "COMPLETED"
        ),
        "candidate_pending": make_match(
            "candidate-pending", resumes["candidate"], jobs["hr_two"], "PENDING"
        ),
        "other_processing": make_match(
            "other-processing", resumes["other"], jobs["hr_one"], "PROCESSING"
        ),
        "hr_both_failed": make_match("hr-both-failed", resumes["hr_one"], jobs["hr_one"], "FAILED"),
        "hr_only_resume": make_match(
            "hr-only-resume", resumes["hr_one"], jobs["hr_two"], "COMPLETED"
        ),
        "hr_only_job": make_match("hr-only-job", resumes["hr_two"], jobs["hr_one"], "COMPLETED"),
        "deleted_resume": make_match(
            "deleted-resume", resumes["deleted"], jobs["hr_one"], "PENDING"
        ),
        "deleted_job": make_match("deleted-job", resumes["hr_two"], jobs["deleted"], "FAILED"),
    }
    session.add_all(matches.values())
    await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    def override_settings() -> Settings:
        return integration_settings

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    tracked_users = {user.id for user in users.values()}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield PostgreSQLMatchingHarness(
                client,
                session,
                users,
                resumes,
                jobs,
                matches,
                integration_settings,
                outer_transaction,
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_settings, None)
        remaining_users: int | None = None
        try:
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()
            remaining_users = await connection.scalar(
                select(func.count()).select_from(User).where(User.id.in_(tracked_users))
            )
        finally:
            if connection.in_transaction():
                await connection.rollback()
            await connection.close()
            await engine.dispose()
        assert remaining_users == 0


@pytest.mark.asyncio
async def test_real_postgres_matching_read_scope_and_filters(
    postgres_matching: PostgreSQLMatchingHarness,
) -> None:
    harness = postgres_matching
    initial = {
        match.id: (match.status, match.generation, match.updated_at)
        for match in harness.matches.values()
    }
    candidate_headers = harness.headers("candidate")
    candidate = await harness.client.get(
        "/api/v1/matching?page=2&limit=1", headers=candidate_headers
    )
    assert candidate.status_code == 200
    assert candidate.json()["meta"] == {
        "page": 2,
        "limit": 1,
        "total_items": 2,
        "total_pages": 2,
    }

    detail = await harness.client.get(
        f"/api/v1/matching/{harness.matches['candidate_completed'].id}",
        headers=candidate_headers,
    )
    denied = await harness.client.get(
        f"/api/v1/matching/{harness.matches['other_processing'].id}",
        headers=candidate_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["matched_skills"][0]["source"] == ("candidate-completed")
    assert denied.status_code == 404

    hr_headers = harness.headers("hr_one")
    hr = await harness.client.get("/api/v1/matching", headers=hr_headers)
    only_resume = await harness.client.get(
        f"/api/v1/matching/{harness.matches['hr_only_resume'].id}", headers=hr_headers
    )
    only_job = await harness.client.get(
        f"/api/v1/matching/{harness.matches['hr_only_job'].id}", headers=hr_headers
    )
    assert hr.json()["meta"]["total_items"] == 1
    assert hr.json()["data"][0]["id"] == str(harness.matches["hr_both_failed"].id)
    assert only_resume.status_code == 404 and only_job.status_code == 404

    admin_headers = harness.headers("admin")
    completed = await harness.client.get(
        "/api/v1/matching",
        params={"status": "COMPLETED", "limit": 100},
        headers=admin_headers,
    )
    filtered = await harness.client.get(
        "/api/v1/matching",
        params={
            "job_id": harness.jobs["hr_one"].id,
            "resume_id": harness.resumes["candidate"].id,
        },
        headers=admin_headers,
    )
    assert completed.json()["meta"]["total_items"] == 3
    assert filtered.json()["meta"]["total_items"] == 1
    for name in ("deleted_resume", "deleted_job"):
        hidden = await harness.client.get(
            f"/api/v1/matching/{harness.matches[name].id}", headers=admin_headers
        )
        assert hidden.status_code == 404

    persisted = list(
        (
            await harness.session.scalars(select(MatchResult).where(MatchResult.id.in_(initial)))
        ).all()
    )
    assert {
        match.id: (match.status, match.generation, match.updated_at) for match in persisted
    } == initial
    assert harness.outer_transaction.is_active


@pytest.mark.asyncio
async def test_real_postgres_gap_analysis_reads_persisted_result(
    postgres_matching: PostgreSQLMatchingHarness,
) -> None:
    harness = postgres_matching
    completed = harness.matches["candidate_completed"]
    candidate_headers = harness.headers("candidate")
    initial = (completed.status, completed.generation, completed.updated_at)
    hr_completed = harness.matches["hr_both_failed"]
    hr_completed.status = "COMPLETED"
    hr_completed.overall_score = Decimal("70.00")
    hr_completed.skill_score = Decimal("65.00")
    hr_completed.semantic_score = Decimal("75.00")
    hr_completed.experience_score = Decimal("70.00")
    hr_completed.matched_skills = [{"skill_id": 1, "name": "Python"}]
    hr_completed.missing_skills = []
    hr_completed.gap_analysis_summary = "Persisted HR summary"
    hr_completed.embedding_model = "integration-model"
    hr_completed.embedding_preprocessing_version = "v1"
    hr_completed.error_message = None
    hr_completed.calculated_at = datetime.now(UTC)
    await harness.session.commit()
    hr_initial = (hr_completed.status, hr_completed.generation, hr_completed.updated_at)
    response = await harness.client.get(
        f"/api/v1/matching/{completed.id}/gap-analysis",
        headers=candidate_headers,
    )
    pending = await harness.client.get(
        f"/api/v1/matching/{harness.matches['candidate_pending'].id}/gap-analysis",
        headers=candidate_headers,
    )
    deleted = await harness.client.get(
        f"/api/v1/matching/{harness.matches['deleted_resume'].id}/gap-analysis",
        headers=harness.headers("admin"),
    )
    hr_response = await harness.client.get(
        f"/api/v1/matching/{hr_completed.id}/gap-analysis",
        headers=harness.headers("hr_one"),
    )
    assert response.status_code == 200
    assert response.json()["data"]["match_id"] == str(completed.id)
    assert response.json()["data"]["matched_skills"][0]["source"] == ("candidate-completed")
    assert response.json()["data"]["recommendation"] == "Improve SQL"
    assert response.json()["data"]["explanation"] is None
    assert pending.status_code == 422
    assert pending.json()["error"]["code"] == "MATCH_NOT_COMPLETED"
    assert deleted.status_code == 404
    assert hr_response.status_code == 200
    assert hr_response.json()["data"]["recommendation"] == "Persisted HR summary"
    await harness.session.refresh(completed)
    await harness.session.refresh(hr_completed)
    assert (completed.status, completed.generation, completed.updated_at) == initial
    assert (hr_completed.status, hr_completed.generation, hr_completed.updated_at) == hr_initial
    assert harness.outer_transaction.is_active
