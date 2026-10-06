from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.ai.resume_parser import RESUME_TEXT_PREPROCESSING_VERSION
from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.core.config import Settings
from app.core.database import create_engine
from app.models.job import JobDescription
from app.models.skill import JobSkill, Skill
from app.models.user import User
from app.services.job_recovery import recover_pending_jobs, select_job_recovery_candidates
from app.workers.job_parse_worker import JobParseTaskOutcome, process_job_parse_task


class UnusedEmbeddingProvider:
    model_name = BGE_M3_MODEL_NAME
    dimension = BGE_M3_EMBEDDING_DIMENSION

    async def embed(self, text: str) -> list[float]:
        raise AssertionError(f"Discarded Job recovery task generated an embedding: {text}")


class RecordingDispatcher:
    def __init__(self, *, fail_id: uuid.UUID | None = None) -> None:
        self.calls: list[tuple[uuid.UUID, int]] = []
        self.fail_id = fail_id

    async def dispatch(self, job_id: uuid.UUID, revision: int) -> None:
        self.calls.append((job_id, revision))
        if job_id == self.fail_id:
            raise RuntimeError("broker unavailable")


@dataclass
class RecoveryHarness:
    connection: AsyncConnection
    transaction: AsyncTransaction
    setup_session: AsyncSession
    session_factory: Callable[[], AsyncSession]
    user: User

    async def create_job(
        self,
        *,
        updated_at: datetime,
        status: str = "PENDING",
        revision: int = 1,
        deleted: bool = False,
    ) -> JobDescription:
        parsed = status == "PARSED"
        job = JobDescription(
            id=uuid.uuid4(),
            recruiter_id=self.user.id,
            title="Recovery Engineer",
            job_level="MID",
            location="Remote",
            raw_content="Requirements\nPython required",
            create_request_fingerprint=uuid.uuid4().hex * 2,
            revision=revision,
            min_experience_years=Decimal("4.0") if parsed else Decimal("0.0"),
            education_requirement="Bachelor's degree" if parsed else None,
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
            is_deleted=deleted,
            deleted_at=updated_at if deleted else None,
            created_at=updated_at,
            updated_at=updated_at,
            parsed_at=updated_at if parsed else None,
        )
        self.setup_session.add(job)
        await self.setup_session.commit()
        return job


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


@pytest_asyncio.fixture
async def recovery_harness() -> AsyncIterator[RecoveryHarness]:
    engine = create_engine(integration_database_settings())
    assert engine is not None
    try:
        connection = await engine.connect()
    except Exception:  # noqa: BLE001 - do not expose configured database credentials
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
    user = User(
        id=uuid.uuid4(),
        email=f"job.recovery.{uuid.uuid4().hex}@example.com",
        password_hash="not-used-by-recovery-integration",
        full_name="Job Recovery Integration",
        phone_number=None,
        role="HR",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    setup_session.add(user)
    await setup_session.commit()
    user_id = user.id
    try:
        yield RecoveryHarness(connection, transaction, setup_session, session_factory, user)
    finally:
        await setup_session.close()
        if transaction.is_active:
            await transaction.rollback()
        remaining = await connection.scalar(
            select(func.count()).select_from(User).where(User.id == user_id)
        )
        if connection.in_transaction():
            await connection.rollback()
        await connection.close()
        await engine.dispose()
        assert remaining == 0


@pytest.mark.asyncio
async def test_real_postgres_selection_is_stale_pending_active_ordered_and_bounded(
    recovery_harness: RecoveryHarness,
) -> None:
    harness = recovery_harness
    now = datetime.now(UTC)
    tied_a = await harness.create_job(updated_at=now - timedelta(minutes=20), revision=2)
    tied_b = await harness.create_job(updated_at=now - timedelta(minutes=20), revision=5)
    newest = await harness.create_job(updated_at=now - timedelta(minutes=10), revision=8)
    await harness.create_job(updated_at=now - timedelta(seconds=30))
    await harness.create_job(updated_at=now - timedelta(minutes=20), status="PROCESSING")
    await harness.create_job(updated_at=now - timedelta(minutes=20), status="PARSED")
    await harness.create_job(updated_at=now - timedelta(minutes=20), status="FAILED")
    await harness.create_job(updated_at=now - timedelta(minutes=20), deleted=True)

    candidates = await select_job_recovery_candidates(
        harness.session_factory,
        cutoff=now - timedelta(minutes=5),
        batch_size=2,
    )

    expected = sorted([(tied_a.id, 2), (tied_b.id, 5)], key=lambda item: item[0])
    assert [(item.job_id, item.revision) for item in candidates] == expected
    assert newest.id not in {item.job_id for item in candidates}


@pytest.mark.asyncio
async def test_recovery_redispatches_current_revision_without_mutation(
    recovery_harness: RecoveryHarness,
) -> None:
    harness = recovery_harness
    now = datetime.now(UTC)
    skill = await harness.setup_session.scalar(select(Skill).order_by(Skill.id.asc()))
    assert skill is not None
    first = await harness.create_job(updated_at=now - timedelta(minutes=20), revision=3)
    second = await harness.create_job(updated_at=now - timedelta(minutes=10), revision=9)
    first_id, second_id = first.id, second.id
    original_updated = {first_id: first.updated_at, second_id: second.updated_at}
    harness.setup_session.add(
        JobSkill(
            job_id=first_id,
            skill_id=skill.id,
            importance="OPTIONAL",
            min_years_required=Decimal("2.0"),
        )
    )
    await harness.setup_session.commit()
    dispatcher = RecordingDispatcher(fail_id=first_id)

    first_result = await recover_pending_jobs(
        harness.session_factory,
        dispatcher,
        grace_seconds=300,
        batch_size=10,
        now=now,
    )
    second_result = await recover_pending_jobs(
        harness.session_factory,
        dispatcher,
        grace_seconds=300,
        batch_size=10,
        now=now,
    )

    assert dispatcher.calls == [
        (first_id, 3),
        (second_id, 9),
        (first_id, 3),
        (second_id, 9),
    ]
    assert first_result == second_result
    assert (first_result.selected, first_result.dispatched, first_result.failed) == (2, 1, 1)
    async with harness.session_factory() as session:
        persisted = list(
            (
                await session.scalars(
                    select(JobDescription)
                    .where(JobDescription.id.in_([first_id, second_id]))
                    .order_by(JobDescription.id)
                )
            ).all()
        )
        stored_skill = await session.scalar(select(JobSkill).where(JobSkill.job_id == first_id))
    assert all(job.parsing_status == "PENDING" for job in persisted)
    assert {job.id: job.revision for job in persisted} == {first_id: 3, second_id: 9}
    assert all(job.updated_at == original_updated[job.id] for job in persisted)
    assert all(job.job_embedding is None and job.parsed_at is None for job in persisted)
    assert stored_skill is not None
    assert stored_skill.importance == "OPTIONAL"
    assert stored_skill.min_years_required == Decimal("2.0")


async def assert_no_parse_aggregate(harness: RecoveryHarness, job_id: uuid.UUID) -> None:
    async with harness.session_factory() as session:
        job = await session.get(JobDescription, job_id)
        assert job is not None
        assert job.job_embedding is None
        assert job.embedding_model is None
        assert job.embedding_preprocessing_version is None
        assert job.parsed_at is None
        skill_count = await session.scalar(
            select(func.count()).select_from(JobSkill).where(JobSkill.job_id == job_id)
        )
    assert skill_count == 0


@pytest.mark.asyncio
async def test_recovered_task_is_discarded_if_job_is_soft_deleted_before_claim(
    recovery_harness: RecoveryHarness,
) -> None:
    harness = recovery_harness
    now = datetime.now(UTC)
    job = await harness.create_job(updated_at=now - timedelta(minutes=20), revision=4)
    candidates = await select_job_recovery_candidates(
        harness.session_factory,
        cutoff=now - timedelta(minutes=5),
        batch_size=10,
    )
    assert [(candidate.job_id, candidate.revision) for candidate in candidates] == [(job.id, 4)]

    deleted_at = datetime.now(UTC)
    async with harness.session_factory() as session:
        await session.execute(
            update(JobDescription)
            .where(JobDescription.id == job.id)
            .values(is_deleted=True, deleted_at=deleted_at, updated_at=deleted_at)
        )
        await session.commit()

    outcome = await process_job_parse_task(
        job.id,
        4,
        session_factory=harness.session_factory,
        embedding_provider=UnusedEmbeddingProvider(),
    )

    assert outcome == JobParseTaskOutcome.DISCARDED
    async with harness.session_factory() as session:
        persisted = await session.get(JobDescription, job.id)
        assert persisted is not None
        assert persisted.is_deleted is True
        assert persisted.deleted_at == deleted_at
        assert persisted.parsing_status == "PENDING"
        assert persisted.revision == 4
    await assert_no_parse_aggregate(harness, job.id)


@pytest.mark.asyncio
async def test_recovered_task_is_discarded_if_revision_advances_before_claim(
    recovery_harness: RecoveryHarness,
) -> None:
    harness = recovery_harness
    now = datetime.now(UTC)
    job = await harness.create_job(updated_at=now - timedelta(minutes=20), revision=6)
    candidates = await select_job_recovery_candidates(
        harness.session_factory,
        cutoff=now - timedelta(minutes=5),
        batch_size=10,
    )
    assert [(candidate.job_id, candidate.revision) for candidate in candidates] == [(job.id, 6)]

    revised_at = datetime.now(UTC)
    async with harness.session_factory() as session:
        await session.execute(
            update(JobDescription)
            .where(JobDescription.id == job.id)
            .values(revision=7, parsing_status="PENDING", updated_at=revised_at)
        )
        await session.commit()

    outcome = await process_job_parse_task(
        job.id,
        6,
        session_factory=harness.session_factory,
        embedding_provider=UnusedEmbeddingProvider(),
    )

    assert outcome == JobParseTaskOutcome.DISCARDED
    async with harness.session_factory() as session:
        persisted = await session.get(JobDescription, job.id)
        assert persisted is not None
        assert persisted.revision == 7
        assert persisted.parsing_status == "PENDING"
    await assert_no_parse_aggregate(harness, job.id)
