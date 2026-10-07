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
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.core.config import Settings, get_settings
from app.core.database import create_engine, get_db_session
from app.core.security import create_access_token
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume
from app.models.skill import JobSkill, Skill
from app.models.user import User
from app.schemas.job_schema import JobCriteriaRequest
from app.services.job_service import update_job_criteria
from main import app

TEST_SECRET = "job-criteria-postgres-jwt-secret"


@dataclass
class CriteriaPostgreSQLHarness:
    client: AsyncClient
    session: AsyncSession
    outer_transaction: AsyncTransaction
    hr: User
    admin: User
    jobs: dict[str, JobDescription]
    resumes: tuple[Resume, Resume]
    matches: dict[str, MatchResult]
    skill_ids: tuple[int, int, int]
    taxonomy_count: int
    settings: Settings

    def headers(self, user: User) -> dict[str, str]:
        token = create_access_token(user.id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def postgres_criteria() -> AsyncIterator[CriteriaPostgreSQLHarness]:
    configured = Settings()
    if configured.database_url is None:
        pytest.skip("DATABASE_URL is not configured")

    database_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=configured.database_url,
        database_connect_timeout_seconds=configured.database_connect_timeout_seconds,
    )
    api_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=None,
        jwt_secret_key=TEST_SECRET,
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
    now = datetime.now(UTC)
    unique = uuid.uuid4().hex
    skill_ids = tuple(
        (await session.scalars(select(Skill.id).order_by(Skill.id.asc()).limit(3))).all()
    )
    if len(skill_ids) < 3:
        await session.close()
        await outer_transaction.rollback()
        await connection.close()
        await engine.dispose()
        pytest.skip("The configured database has fewer than three canonical skills")
    typed_skill_ids = (skill_ids[0], skill_ids[1], skill_ids[2])
    taxonomy_count = await session.scalar(select(func.count()).select_from(Skill)) or 0

    def user(role: str, name: str) -> User:
        return User(
            id=uuid.uuid4(),
            email=f"criteria.{unique}.{name}@example.com",
            password_hash="not-used",
            full_name=f"Criteria {name}",
            phone_number=None,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    hr = user("HR", "hr")
    admin = user("ADMIN", "admin")
    session.add_all((hr, admin))
    await session.flush()

    def job(name: str, *, active: bool, verified: bool, revision: int) -> JobDescription:
        return JobDescription(
            id=uuid.uuid4(),
            recruiter_id=hr.id,
            title=f"Criteria {unique} {name}",
            job_level="SENIOR",
            location="Hanoi",
            raw_content=f"Canonical raw content {name}",
            create_request_fingerprint=uuid.uuid4().hex * 2,
            revision=revision,
            min_experience_years=Decimal("2.0"),
            education_requirement="Bachelor",
            job_embedding=[0.25] * 1024,
            embedding_model="BAAI/bge-m3",
            embedding_preprocessing_version="semantic-v1",
            parsing_status="PARSED",
            parsing_error_message=None,
            is_criteria_verified=verified,
            w_skill=Decimal("0.500"),
            w_semantic=Decimal("0.300"),
            w_experience=Decimal("0.200"),
            status="ACTIVE" if active else "DRAFT",
            is_deleted=False,
            created_at=now,
            updated_at=now,
            parsed_at=now,
            deleted_at=None,
        )

    jobs = {
        "success": job("success", active=True, verified=True, revision=4),
        "atomic": job("atomic", active=False, verified=False, revision=6),
    }
    session.add_all(jobs.values())

    def resume(name: str, revision: int, *, deleted: bool) -> Resume:
        return Resume(
            id=uuid.uuid4(),
            owner_user_id=hr.id,
            file_name=f"{name}.pdf",
            storage_key=f"criteria/{unique}/{name}",
            file_size=128,
            mime_type="application/pdf",
            create_request_fingerprint=uuid.uuid4().hex * 2,
            revision=revision,
            parsing_status="PARSED",
            raw_text=f"Resume {name}",
            resume_embedding=[0.25] * 1024,
            embedding_model="BAAI/bge-m3",
            embedding_preprocessing_version="semantic-v1",
            error_message=None,
            is_manually_edited=False,
            is_deleted=deleted,
            created_at=now,
            updated_at=now,
            parsed_at=now,
            deleted_at=now if deleted else None,
        )

    resumes = (resume("one", 2, deleted=False), resume("two", 8, deleted=True))
    session.add_all(resumes)
    await session.flush()

    session.add_all(
        (
            JobSkill(
                job_id=jobs["success"].id,
                skill_id=typed_skill_ids[0],
                importance="OPTIONAL",
                min_years_required=Decimal("1.0"),
            ),
            JobSkill(
                job_id=jobs["atomic"].id,
                skill_id=typed_skill_ids[1],
                importance="MANDATORY",
                min_years_required=Decimal("2.0"),
            ),
        )
    )

    def completed_match(
        job_value: JobDescription, resume_value: Resume, generation: int
    ) -> MatchResult:
        return MatchResult(
            id=uuid.uuid4(),
            job_id=job_value.id,
            resume_id=resume_value.id,
            generation=generation,
            resume_revision=1,
            job_revision=1,
            overall_score=Decimal("88.00"),
            skill_score=Decimal("90.00"),
            semantic_score=Decimal("80.00"),
            experience_score=Decimal("95.00"),
            matched_skills=[{"skill_id": typed_skill_ids[0]}],
            missing_skills=[{"skill_id": typed_skill_ids[1]}],
            gap_analysis_summary="stale",
            algorithm_version="hybrid-v1",
            embedding_model="old-model",
            embedding_preprocessing_version="old-version",
            status="COMPLETED",
            error_message=None,
            created_at=now,
            updated_at=now,
            calculated_at=now,
        )

    matches = {
        "completed": completed_match(jobs["success"], resumes[0], 2),
        "deleted_resume": MatchResult(
            id=uuid.uuid4(),
            job_id=jobs["success"].id,
            resume_id=resumes[1].id,
            generation=7,
            resume_revision=1,
            job_revision=1,
            overall_score=None,
            skill_score=None,
            semantic_score=None,
            experience_score=None,
            matched_skills=[],
            missing_skills=[],
            gap_analysis_summary=None,
            algorithm_version="hybrid-v1",
            embedding_model=None,
            embedding_preprocessing_version=None,
            status="FAILED",
            error_message="stale failure",
            created_at=now,
            updated_at=now,
            calculated_at=None,
        ),
        "atomic": completed_match(jobs["atomic"], resumes[0], 11),
    }
    session.add_all(matches.values())
    await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    def override_settings() -> Settings:
        return api_settings

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    tracked_ids = {hr.id, admin.id}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield CriteriaPostgreSQLHarness(
                client=client,
                session=session,
                outer_transaction=outer_transaction,
                hr=hr,
                admin=admin,
                jobs=jobs,
                resumes=resumes,
                matches=matches,
                skill_ids=typed_skill_ids,
                taxonomy_count=taxonomy_count,
                settings=api_settings,
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
                select(func.count()).select_from(User).where(User.id.in_(tracked_ids))
            )
        finally:
            if connection.in_transaction():
                await connection.rollback()
            await connection.close()
            await engine.dispose()
        assert remaining_users == 0


@pytest.mark.asyncio
async def test_real_postgres_criteria_replacement_and_match_invalidation(
    postgres_criteria: CriteriaPostgreSQLHarness,
) -> None:
    harness = postgres_criteria
    job = harness.jobs["success"]
    job_id = job.id
    match_ids = {name: match.id for name, match in harness.matches.items()}
    expected_resume_revisions = {resume.id: resume.revision for resume in harness.resumes}
    original = {
        "raw_content": job.raw_content,
        "parsing_status": job.parsing_status,
        "parsing_error_message": job.parsing_error_message,
        "embedding": tuple(job.job_embedding or ()),
        "model": job.embedding_model,
        "preprocessing": job.embedding_preprocessing_version,
        "parsed_at": job.parsed_at,
        "fingerprint": job.create_request_fingerprint,
        "recruiter_id": job.recruiter_id,
        "status": job.status,
    }
    initial_generations = {name: match.generation for name, match in harness.matches.items()}

    response = await harness.client.put(
        f"/api/v1/jobs/{job_id}/criteria",
        json={
            "min_experience_years": 3.5,
            "education_requirement": None,
            "skills": [
                {
                    "skill_id": harness.skill_ids[2],
                    "importance": "OPTIONAL",
                    "min_years_required": 1.5,
                },
                {
                    "skill_id": harness.skill_ids[1],
                    "importance": "MANDATORY",
                    "min_years_required": 3.0,
                },
            ],
        },
        headers=harness.headers(harness.hr),
    )
    assert response.status_code == 200
    assert response.json()["data"]["revision"] == 5
    assert [item["skill_id"] for item in response.json()["data"]["skills"]] == sorted(
        harness.skill_ids[1:]
    )

    harness.session.expire_all()
    persisted_job = await harness.session.get(JobDescription, job_id)
    assert persisted_job is not None
    assert persisted_job.revision == 5
    assert persisted_job.min_experience_years == Decimal("3.5")
    assert persisted_job.education_requirement is None
    assert persisted_job.is_criteria_verified is True
    assert {
        "raw_content": persisted_job.raw_content,
        "parsing_status": persisted_job.parsing_status,
        "parsing_error_message": persisted_job.parsing_error_message,
        "embedding": tuple(persisted_job.job_embedding or ()),
        "model": persisted_job.embedding_model,
        "preprocessing": persisted_job.embedding_preprocessing_version,
        "parsed_at": persisted_job.parsed_at,
        "fingerprint": persisted_job.create_request_fingerprint,
        "recruiter_id": persisted_job.recruiter_id,
        "status": persisted_job.status,
    } == original

    persisted_skills = (
        await harness.session.scalars(
            select(JobSkill).where(JobSkill.job_id == job_id).order_by(JobSkill.skill_id.asc())
        )
    ).all()
    assert [item.skill_id for item in persisted_skills] == sorted(harness.skill_ids[1:])
    assert [item.importance for item in persisted_skills] == ["MANDATORY", "OPTIONAL"]
    assert (
        await harness.session.scalar(select(func.count()).select_from(Skill))
        == harness.taxonomy_count
    )

    persisted_matches = (
        await harness.session.scalars(
            select(MatchResult).where(MatchResult.job_id == job_id).order_by(MatchResult.id.asc())
        )
    ).all()
    for match in persisted_matches:
        name = "completed" if match.id == match_ids["completed"] else "deleted_resume"
        assert match.generation == initial_generations[name] + 1
        assert match.resume_revision == expected_resume_revisions[match.resume_id]
        assert match.job_revision == 5
        assert match.status == "PENDING"
        assert match.overall_score is None
        assert match.skill_score is None
        assert match.semantic_score is None
        assert match.experience_score is None
        assert match.matched_skills == []
        assert match.missing_skills == []
        assert match.gap_analysis_summary is None
        assert match.error_message is None
        assert match.embedding_model is None
        assert match.embedding_preprocessing_version is None
        assert match.calculated_at is None
    assert harness.outer_transaction.is_active


@pytest.mark.asyncio
async def test_real_postgres_criteria_validation_is_non_mutating(
    postgres_criteria: CriteriaPostgreSQLHarness,
) -> None:
    harness = postgres_criteria
    job = harness.jobs["atomic"]
    job_id = job.id
    before = (job.revision, job.is_criteria_verified, job.min_experience_years)
    response = await harness.client.put(
        f"/api/v1/jobs/{job_id}/criteria",
        json={"skills": [{"skill_id": 2147483647, "importance": "MANDATORY"}]},
        headers=harness.headers(harness.admin),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_JOB_CRITERIA"
    harness.session.expire_all()
    persisted = await harness.session.get(JobDescription, job_id)
    assert persisted is not None
    assert (
        persisted.revision,
        persisted.is_criteria_verified,
        persisted.min_experience_years,
    ) == before


@pytest.mark.asyncio
async def test_real_postgres_criteria_omitted_job_fields_are_preserved(
    postgres_criteria: CriteriaPostgreSQLHarness,
) -> None:
    harness = postgres_criteria
    job = harness.jobs["atomic"]
    job_id = job.id
    before = (job.min_experience_years, job.education_requirement)
    response = await harness.client.put(
        f"/api/v1/jobs/{job_id}/criteria",
        json={"skills": [{"skill_id": harness.skill_ids[2], "importance": "OPTIONAL"}]},
        headers=harness.headers(harness.hr),
    )
    assert response.status_code == 200
    persisted = await harness.session.get(JobDescription, job_id)
    assert persisted is not None
    assert (persisted.min_experience_years, persisted.education_requirement) == before


@pytest.mark.asyncio
async def test_real_postgres_criteria_failure_rolls_back_complete_transaction(
    postgres_criteria: CriteriaPostgreSQLHarness,
) -> None:
    harness = postgres_criteria
    job = harness.jobs["atomic"]
    match = harness.matches["atomic"]
    job_id = job.id
    match_id = match.id
    old_job = (
        job.revision,
        job.is_criteria_verified,
        job.min_experience_years,
        job.education_requirement,
    )
    old_skills = tuple(
        (
            await harness.session.scalars(
                select(JobSkill).where(JobSkill.job_id == job_id).order_by(JobSkill.skill_id.asc())
            )
        ).all()
    )
    old_skill_values = tuple(
        (item.skill_id, item.importance, item.min_years_required) for item in old_skills
    )
    old_match = (
        match.generation,
        match.status,
        match.overall_score,
        match.matched_skills,
        match.embedding_model,
        match.calculated_at,
    )
    payload = JobCriteriaRequest.model_validate(
        {
            "min_experience_years": 4.0,
            "education_requirement": None,
            "skills": [{"skill_id": harness.skill_ids[2], "importance": "OPTIONAL"}],
        }
    )

    original_commit = harness.session.commit

    async def fail_after_flush() -> None:
        await harness.session.flush()
        raise SQLAlchemyError("forced criteria transaction failure")

    harness.session.commit = fail_after_flush  # type: ignore[method-assign]
    try:
        with pytest.raises(SQLAlchemyError, match="forced criteria transaction failure"):
            await update_job_criteria(
                harness.session,
                current_user=harness.hr,
                job_id=job_id,
                payload=payload,
            )
    finally:
        harness.session.commit = original_commit  # type: ignore[method-assign]

    persisted_job = await harness.session.get(JobDescription, job_id)
    assert persisted_job is not None
    assert (
        persisted_job.revision,
        persisted_job.is_criteria_verified,
        persisted_job.min_experience_years,
        persisted_job.education_requirement,
    ) == old_job
    persisted_skills = tuple(
        (
            await harness.session.scalars(
                select(JobSkill).where(JobSkill.job_id == job_id).order_by(JobSkill.skill_id.asc())
            )
        ).all()
    )
    assert (
        tuple(
            (item.skill_id, item.importance, item.min_years_required) for item in persisted_skills
        )
        == old_skill_values
    )
    persisted_match = await harness.session.get(MatchResult, match_id)
    assert persisted_match is not None
    assert (
        persisted_match.generation,
        persisted_match.status,
        persisted_match.overall_score,
        persisted_match.matched_skills,
        persisted_match.embedding_model,
        persisted_match.calculated_at,
    ) == old_match
    assert harness.outer_transaction.is_active
