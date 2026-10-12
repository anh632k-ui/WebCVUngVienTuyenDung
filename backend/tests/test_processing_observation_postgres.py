from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.ai.resume_parser import RESUME_TEXT_PREPROCESSING_VERSION
from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.core.config import Settings
from app.core.database import create_engine
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import CandidateProfile, Resume
from app.models.skill import JobSkill, ResumeSkill, Skill
from app.models.user import User
from app.services import processing_observation
from app.services.processing_observation import observe_old_processing, select_old_processing


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


@dataclass
class ObservationHarness:
    connection: AsyncConnection
    transaction: AsyncTransaction
    setup_session: AsyncSession
    session_factory: Callable[[], AsyncSession]
    candidate: User
    recruiter: User

    async def create_resume(
        self,
        *,
        updated_at: datetime,
        status: str = "PROCESSING",
        revision: int = 1,
        resource_id: uuid.UUID | None = None,
        raw_text: str | None = None,
    ) -> Resume:
        resume_id = resource_id or uuid.uuid4()
        source = f"observation-{resume_id}".encode()
        parsed = status == "PARSED"
        resume = Resume(
            id=resume_id,
            owner_user_id=self.candidate.id,
            file_name="observer.pdf",
            storage_key=f"processing-observer/{resume_id}/source",
            file_size=len(source),
            mime_type="application/pdf",
            create_request_fingerprint=hashlib.sha256(source).hexdigest(),
            revision=revision,
            parsing_status=status,
            raw_text="parsed resume" if parsed else raw_text,
            resume_embedding=[0.0] * BGE_M3_EMBEDDING_DIMENSION if parsed else None,
            embedding_model=BGE_M3_MODEL_NAME if parsed else None,
            embedding_preprocessing_version=(RESUME_TEXT_PREPROCESSING_VERSION if parsed else None),
            error_message="controlled failure" if status == "FAILED" else None,
            is_manually_edited=False,
            is_deleted=False,
            created_at=updated_at,
            updated_at=updated_at,
            parsed_at=updated_at if parsed else None,
        )
        self.setup_session.add(resume)
        await self.setup_session.commit()
        return resume

    async def create_job(
        self,
        *,
        updated_at: datetime,
        status: str = "PROCESSING",
        revision: int = 1,
        resource_id: uuid.UUID | None = None,
        raw_content: str = "PRIVATE JD CONTENT",
    ) -> JobDescription:
        parsed = status == "PARSED"
        job = JobDescription(
            id=resource_id or uuid.uuid4(),
            recruiter_id=self.recruiter.id,
            title="Observer QA Engineer",
            job_level="MID",
            location="Remote",
            raw_content=raw_content,
            create_request_fingerprint=uuid.uuid4().hex * 2,
            revision=revision,
            min_experience_years=Decimal("2.0") if parsed else Decimal("0.0"),
            education_requirement=None,
            job_embedding=[0.0] * BGE_M3_EMBEDDING_DIMENSION if parsed else None,
            embedding_model=BGE_M3_MODEL_NAME if parsed else None,
            embedding_preprocessing_version=(RESUME_TEXT_PREPROCESSING_VERSION if parsed else None),
            parsing_status=status,
            parsing_error_message="controlled failure" if status == "FAILED" else None,
            is_criteria_verified=False,
            w_skill=Decimal("0.500"),
            w_semantic=Decimal("0.300"),
            w_experience=Decimal("0.200"),
            status="DRAFT",
            is_deleted=False,
            created_at=updated_at,
            updated_at=updated_at,
            parsed_at=updated_at if parsed else None,
        )
        self.setup_session.add(job)
        await self.setup_session.commit()
        return job

    async def create_match(
        self,
        resume: Resume,
        job: JobDescription,
        *,
        updated_at: datetime,
        status: str = "PROCESSING",
        generation: int = 1,
        resource_id: uuid.UUID | None = None,
    ) -> MatchResult:
        completed = status == "COMPLETED"
        match = MatchResult(
            id=resource_id or uuid.uuid4(),
            job_id=job.id,
            resume_id=resume.id,
            generation=generation,
            resume_revision=resume.revision,
            job_revision=job.revision,
            overall_score=Decimal("80.00") if completed else None,
            skill_score=Decimal("80.00") if completed else None,
            semantic_score=Decimal("80.00") if completed else None,
            experience_score=Decimal("80.00") if completed else None,
            matched_skills=[],
            missing_skills=[],
            gap_analysis_summary=None,
            algorithm_version="hybrid-v1",
            embedding_model=BGE_M3_MODEL_NAME if completed else None,
            embedding_preprocessing_version=(
                RESUME_TEXT_PREPROCESSING_VERSION if completed else None
            ),
            status=status,
            error_message="controlled failure" if status == "FAILED" else None,
            created_at=updated_at,
            updated_at=updated_at,
            calculated_at=updated_at if completed else None,
        )
        self.setup_session.add(match)
        await self.setup_session.commit()
        return match

    async def create_parsed_links(self, *, updated_at: datetime) -> tuple[Resume, JobDescription]:
        resume = await self.create_resume(updated_at=updated_at, status="PARSED")
        job = await self.create_job(updated_at=updated_at, status="PARSED")
        return resume, job


@pytest_asyncio.fixture
async def observation_harness() -> AsyncIterator[ObservationHarness]:
    engine = create_engine(integration_database_settings())
    assert engine is not None
    try:
        connection = await engine.connect()
    except Exception:  # noqa: BLE001 - never expose connection credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)
    transaction = await connection.begin()

    def session_factory() -> AsyncSession:
        return AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

    setup_session = session_factory()
    now = datetime.now(UTC)
    candidate = User(
        id=uuid.uuid4(),
        email=f"processing.observer.{uuid.uuid4().hex}.candidate@example.com",
        password_hash="not-used-by-observer-integration",
        full_name="Observer Candidate",
        role="CANDIDATE",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    recruiter = User(
        id=uuid.uuid4(),
        email=f"processing.observer.{uuid.uuid4().hex}.hr@example.com",
        password_hash="not-used-by-observer-integration",
        full_name="Observer Recruiter",
        role="HR",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    setup_session.add_all([candidate, recruiter])
    await setup_session.commit()
    user_ids = (candidate.id, recruiter.id)
    try:
        yield ObservationHarness(
            connection,
            transaction,
            setup_session,
            session_factory,
            candidate,
            recruiter,
        )
    finally:
        await setup_session.close()
        if transaction.is_active:
            await transaction.rollback()
        remaining = await connection.scalar(
            select(func.count()).select_from(User).where(User.id.in_(user_ids))
        )
        if connection.in_transaction():
            await connection.rollback()
        await connection.close()
        await engine.dispose()
        assert remaining == 0


@pytest.mark.asyncio
async def test_real_postgres_selects_metadata_for_all_processing_resource_types(
    observation_harness: ObservationHarness,
) -> None:
    harness = observation_harness
    now = datetime.now(UTC)
    updated_at = now - timedelta(hours=2)
    resume = await harness.create_resume(
        updated_at=updated_at,
        revision=3,
        raw_text="PRIVATE CV CONTENT",
    )
    job = await harness.create_job(updated_at=updated_at, revision=5)
    match = await harness.create_match(resume, job, updated_at=updated_at, generation=7)

    candidates = await select_old_processing(
        harness.session_factory,
        cutoff=now - timedelta(hours=1),
        batch_size=10,
    )

    by_id = {candidate.resource_id: candidate for candidate in candidates}
    assert set(by_id) == {resume.id, job.id, match.id}
    assert by_id[resume.id].snapshot_version == 3
    assert by_id[job.id].snapshot_version == 5
    assert by_id[match.id].snapshot_version == 7
    assert by_id[match.id].resume_revision == 3
    assert by_id[match.id].job_revision == 5
    assert all(candidate.updated_at == updated_at for candidate in candidates)
    assert all(
        set(vars(candidate))
        <= {
            "resource_kind",
            "resource_id",
            "snapshot_version",
            "updated_at",
            "is_deleted",
            "resume_revision",
            "job_revision",
        }
        for candidate in candidates
    )


@pytest.mark.asyncio
async def test_real_postgres_filters_state_recency_and_includes_cutoff_boundary(
    observation_harness: ObservationHarness,
) -> None:
    harness = observation_harness
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=30)
    old = cutoff - timedelta(seconds=1)
    included_resume = await harness.create_resume(updated_at=old, revision=2)
    boundary_job = await harness.create_job(updated_at=cutoff, revision=4)
    await harness.create_resume(updated_at=cutoff + timedelta(microseconds=1), revision=8)
    for status in ("PENDING", "PARSED", "FAILED"):
        await harness.create_resume(updated_at=old, status=status)
        await harness.create_job(updated_at=old, status=status)
    linked_resume, linked_job = await harness.create_parsed_links(updated_at=old)
    included_match = await harness.create_match(
        linked_resume, linked_job, updated_at=old, generation=6
    )
    recent_links = await harness.create_parsed_links(updated_at=old)
    await harness.create_match(
        *recent_links,
        updated_at=cutoff + timedelta(seconds=1),
        generation=9,
    )
    for status in ("PENDING", "COMPLETED", "FAILED"):
        links = await harness.create_parsed_links(updated_at=old)
        await harness.create_match(*links, updated_at=old, status=status)

    candidates = await select_old_processing(
        harness.session_factory,
        cutoff=cutoff,
        batch_size=20,
    )

    assert {candidate.resource_id for candidate in candidates} == {
        included_resume.id,
        boundary_job.id,
        included_match.id,
    }


@pytest.mark.asyncio
async def test_real_postgres_applies_global_deterministic_batch_order(
    observation_harness: ObservationHarness,
) -> None:
    harness = observation_harness
    updated_at = datetime.now(UTC) - timedelta(hours=2)
    resources: list[tuple[str, uuid.UUID]] = []
    for value in (101, 102):
        resume = await harness.create_resume(
            updated_at=updated_at,
            resource_id=uuid.UUID(int=value),
        )
        resources.append(("resume", resume.id))
    for value in (201, 202):
        job = await harness.create_job(
            updated_at=updated_at,
            resource_id=uuid.UUID(int=value),
        )
        resources.append(("job", job.id))
    links = await harness.create_parsed_links(updated_at=updated_at - timedelta(seconds=1))
    match = await harness.create_match(
        *links,
        updated_at=updated_at,
        resource_id=uuid.UUID(int=301),
    )
    resources.append(("match", match.id))

    candidates = await select_old_processing(
        harness.session_factory,
        cutoff=updated_at,
        batch_size=4,
    )

    expected = sorted(resources, key=lambda item: (updated_at, item[0], item[1]))[:4]
    assert [(item.resource_kind, item.resource_id) for item in candidates] == expected
    assert len(candidates) == 4


async def _business_snapshot(
    harness: ObservationHarness,
    resume_id: uuid.UUID,
    job_id: uuid.UUID,
    match_id: uuid.UUID,
) -> tuple[Any, ...]:
    async with harness.session_factory() as session:
        resume = (
            await session.execute(
                select(
                    Resume.revision,
                    Resume.parsing_status,
                    Resume.updated_at,
                    Resume.raw_text,
                ).where(Resume.id == resume_id)
            )
        ).one()
        job = (
            await session.execute(
                select(
                    JobDescription.revision,
                    JobDescription.parsing_status,
                    JobDescription.updated_at,
                    JobDescription.raw_content,
                ).where(JobDescription.id == job_id)
            )
        ).one()
        match = (
            await session.execute(
                select(MatchResult.generation, MatchResult.status, MatchResult.updated_at).where(
                    MatchResult.id == match_id
                )
            )
        ).one()
        aggregate_counts = []
        for model, foreign_key, resource_id in (
            (CandidateProfile, CandidateProfile.resume_id, resume_id),
            (ResumeSkill, ResumeSkill.resume_id, resume_id),
            (JobSkill, JobSkill.job_id, job_id),
        ):
            aggregate_counts.append(
                await session.scalar(
                    select(func.count()).select_from(model).where(foreign_key == resource_id)
                )
            )
    return resume, job, match, tuple(aggregate_counts)


@pytest.mark.asyncio
async def test_real_postgres_observer_executes_three_selects_and_mutates_nothing(
    observation_harness: ObservationHarness,
) -> None:
    harness = observation_harness
    updated_at = datetime.now(UTC) - timedelta(hours=2)
    resume = await harness.create_resume(
        updated_at=updated_at,
        revision=11,
        raw_text="PRIVATE CV SNAPSHOT",
    )
    job = await harness.create_job(
        updated_at=updated_at,
        revision=13,
        raw_content="PRIVATE JD SNAPSHOT",
    )
    match = await harness.create_match(resume, job, updated_at=updated_at, generation=17)
    skill = await harness.setup_session.scalar(select(Skill).order_by(Skill.id.asc()))
    assert skill is not None
    harness.setup_session.add_all(
        [
            CandidateProfile(resume_id=resume.id, full_name="Private Candidate"),
            ResumeSkill(resume_id=resume.id, skill_id=skill.id),
            JobSkill(
                job_id=job.id,
                skill_id=skill.id,
                importance="OPTIONAL",
                min_years_required=Decimal("1.0"),
            ),
        ]
    )
    await harness.setup_session.commit()
    before = await _business_snapshot(harness, resume.id, job.id, match.id)
    statements: list[str] = []

    def record_statement(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, context, executemany
        statements.append(statement.lstrip().split(maxsplit=1)[0].upper())

    event.listen(harness.connection.sync_connection, "before_cursor_execute", record_statement)
    try:
        await select_old_processing(
            harness.session_factory,
            cutoff=updated_at,
            batch_size=10,
        )
    finally:
        event.remove(
            harness.connection.sync_connection,
            "before_cursor_execute",
            record_statement,
        )
    after = await _business_snapshot(harness, resume.id, job.id, match.id)

    assert statements.count("SELECT") == 3
    assert not {"INSERT", "UPDATE", "DELETE"}.intersection(statements)
    assert after == before


@pytest.mark.asyncio
async def test_real_postgres_observer_logs_only_bounded_sample_counts(
    observation_harness: ObservationHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = observation_harness
    now = datetime.now(UTC)
    updated_at = now - timedelta(hours=2)
    resume = await harness.create_resume(
        updated_at=updated_at,
        raw_text="NEVER LOG THIS CV SECRET",
    )
    job = await harness.create_job(
        updated_at=updated_at,
        raw_content="NEVER LOG THIS JD SECRET",
    )
    match = await harness.create_match(resume, job, updated_at=updated_at)
    caplog.set_level(logging.WARNING, logger=processing_observation.__name__)
    settings = Settings(
        _env_file=None,
        processing_observation_enabled=True,
        processing_observation_threshold_seconds=1_800,
        processing_observation_batch_size=2,
    )

    report = await observe_old_processing(settings, harness.session_factory, now=now)

    counts = {
        kind: sum(item.resource_kind == kind for item in report.candidates)
        for kind in ("resume", "job", "match")
    }
    assert len(report.candidates) == 2
    assert f"sampled_resumes={counts['resume']}" in caplog.text
    assert f"sampled_jobs={counts['job']}" in caplog.text
    assert f"sampled_matches={counts['match']}" in caplog.text
    assert "sample_size=2" in caplog.text
    assert "batch_limit=2" in caplog.text
    assert str(resume.id) not in caplog.text
    assert str(job.id) not in caplog.text
    assert str(match.id) not in caplog.text
    assert "NEVER LOG THIS CV SECRET" not in caplog.text
    assert "NEVER LOG THIS JD SECRET" not in caplog.text
