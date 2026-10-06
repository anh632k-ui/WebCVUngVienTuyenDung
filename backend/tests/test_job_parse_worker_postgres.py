from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.ai.job_schemas import ParsedJobResult, ParsedJobSkill
from app.ai.resume_parser import RESUME_TEXT_PREPROCESSING_VERSION
from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.core.config import Settings
from app.core.database import create_engine
from app.core.version_guard import claim_job_revision
from app.models.job import JobDescription
from app.models.skill import JobSkill, Skill
from app.models.user import User
from app.workers import job_parse_worker
from app.workers.job_parse_worker import JobParseTaskOutcome, process_job_parse_task


class FakeEmbeddingProvider:
    model_name = BGE_M3_MODEL_NAME
    dimension = BGE_M3_EMBEDDING_DIMENSION

    async def embed(self, text: str) -> list[float]:
        assert text
        return [0.125] * self.dimension


class FailingEmbeddingProvider(FakeEmbeddingProvider):
    async def embed(self, text: str) -> list[float]:
        del text
        raise RuntimeError("C:/private/huggingface/cache/model")


class MutatingEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self, mutation: Callable[[], Awaitable[None]]) -> None:
        self.mutation = mutation

    async def embed(self, text: str) -> list[float]:
        values = await super().embed(text)
        await self.mutation()
        return values


class MutatingFailingEmbeddingProvider(MutatingEmbeddingProvider):
    async def embed(self, text: str) -> list[float]:
        del text
        await self.mutation()
        raise RuntimeError("private embedding failure")


@dataclass
class PostgreSQLJobWorkerHarness:
    connection: AsyncConnection
    outer_transaction: AsyncTransaction
    setup_session: AsyncSession
    session_factory: Any
    user: User

    async def create_job(
        self,
        raw_content: str = "Requirements\nPython required",
        *,
        parsing_status: str = "PENDING",
        revision: int = 1,
        deleted: bool = False,
        criteria_verified: bool = False,
        business_status: str = "DRAFT",
    ) -> JobDescription:
        now = datetime.now(UTC)
        is_parsed = parsing_status == "PARSED"
        job = JobDescription(
            id=uuid.uuid4(),
            recruiter_id=self.user.id,
            title="Worker Integration Engineer",
            job_level="MID",
            location="Remote",
            raw_content=raw_content,
            create_request_fingerprint=uuid.uuid4().hex * 2,
            revision=revision,
            min_experience_years=Decimal("0.0"),
            education_requirement=None,
            job_embedding=[0.0] * BGE_M3_EMBEDDING_DIMENSION if is_parsed else None,
            embedding_model=BGE_M3_MODEL_NAME if is_parsed else None,
            embedding_preprocessing_version=(
                RESUME_TEXT_PREPROCESSING_VERSION if is_parsed else None
            ),
            parsing_status=parsing_status,
            parsing_error_message="prior failure" if parsing_status == "FAILED" else None,
            is_criteria_verified=criteria_verified,
            w_skill=Decimal("0.500"),
            w_semantic=Decimal("0.300"),
            w_experience=Decimal("0.200"),
            status=business_status,
            is_deleted=deleted,
            created_at=now,
            updated_at=now,
            parsed_at=now if is_parsed else None,
            deleted_at=now if deleted else None,
        )
        self.setup_session.add(job)
        await self.setup_session.commit()
        return job

    async def load_job(self, job_id: uuid.UUID) -> JobDescription:
        async with self.session_factory() as session:
            job = await session.get(JobDescription, job_id)
            assert job is not None
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


def make_user(label: str) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=f"job.worker.{label}.{uuid.uuid4().hex}@example.com",
        password_hash="not-used-by-worker-integration",
        full_name="Job Worker Integration",
        phone_number=None,
        role="HR",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


@pytest_asyncio.fixture
async def postgres_job_worker() -> AsyncIterator[PostgreSQLJobWorkerHarness]:
    settings = integration_database_settings()
    engine = create_engine(settings)
    assert engine is not None
    connection: AsyncConnection | None = None
    try:
        connection = await engine.connect()
    except Exception:  # noqa: BLE001 - never expose database credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)

    outer_transaction = await connection.begin()

    def session_factory() -> AsyncSession:
        return AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

    setup_session = session_factory()
    user = make_user("rollback")
    user_id = user.id
    setup_session.add(user)
    await setup_session.commit()
    harness = PostgreSQLJobWorkerHarness(
        connection,
        outer_transaction,
        setup_session,
        session_factory,
        user,
    )
    try:
        yield harness
    finally:
        await setup_session.close()
        if outer_transaction.is_active:
            await outer_transaction.rollback()
        remaining = await connection.scalar(
            select(func.count()).select_from(User).where(User.id == user_id)
        )
        if connection.in_transaction():
            await connection.rollback()
        await connection.close()
        await engine.dispose()
        assert remaining == 0


@pytest.mark.asyncio
async def test_claim_cas_accepts_only_pending_exact_revision_and_not_deleted(
    postgres_job_worker: PostgreSQLJobWorkerHarness,
) -> None:
    harness = postgres_job_worker
    pending = await harness.create_job()
    stale = await harness.create_job(revision=2)
    deleted_job = await harness.create_job(deleted=True)
    processing = await harness.create_job(parsing_status="PROCESSING")
    parsed = await harness.create_job(parsing_status="PARSED")
    failed = await harness.create_job(parsing_status="FAILED")

    async def claim(job_id: uuid.UUID, revision: int) -> bool:
        async with harness.session_factory() as session:
            return await claim_job_revision(
                session,
                job_id=job_id,
                expected_revision=revision,
            )

    assert await claim(pending.id, 1) is True
    assert await claim(pending.id, 1) is False
    assert await claim(stale.id, 1) is False
    assert await claim(deleted_job.id, 1) is False
    assert await claim(processing.id, 1) is False
    assert await claim(parsed.id, 1) is False
    assert await claim(failed.id, 1) is False
    claimed_row = await harness.load_job(pending.id)
    assert claimed_row.parsing_status == "PROCESSING"
    assert claimed_row.revision == 1


@pytest.mark.asyncio
async def test_real_parser_taxonomy_embedding_and_aggregate_success(
    postgres_job_worker: PostgreSQLJobWorkerHarness,
) -> None:
    harness = postgres_job_worker
    skill = await harness.setup_session.scalar(select(Skill).order_by(Skill.id.asc()))
    assert skill is not None
    taxonomy_count_before = await harness.setup_session.scalar(
        select(func.count()).select_from(Skill)
    )
    raw_content = (
        "Backend Engineer\n"
        "Requirements\n"
        "At least 3 years of professional experience.\n"
        "Bachelor's degree in Computer Science\n"
        f"{skill.name} 2+ years required"
    )
    job = await harness.create_job(raw_content)
    harness.setup_session.add(
        JobSkill(
            job_id=job.id,
            skill_id=skill.id,
            importance="OPTIONAL",
            min_years_required=Decimal("9.0"),
        )
    )
    await harness.setup_session.commit()

    first = await process_job_parse_task(
        job.id,
        1,
        session_factory=harness.session_factory,
        embedding_provider=FakeEmbeddingProvider(),
    )
    duplicate = await process_job_parse_task(
        job.id,
        1,
        session_factory=harness.session_factory,
        embedding_provider=FakeEmbeddingProvider(),
    )

    async with harness.session_factory() as session:
        stored = await session.get(JobDescription, job.id)
        job_skills = (
            await session.scalars(
                select(JobSkill).where(JobSkill.job_id == job.id).order_by(JobSkill.skill_id.asc())
            )
        ).all()
        taxonomy_count_after = await session.scalar(select(func.count()).select_from(Skill))

    assert first is JobParseTaskOutcome.PARSED
    assert duplicate is JobParseTaskOutcome.DISCARDED
    assert stored is not None and stored.parsing_status == "PARSED"
    assert stored.revision == 1
    assert stored.min_experience_years == Decimal("3.0")
    assert stored.education_requirement == "Bachelor's degree in Computer Science"
    assert stored.job_embedding is not None and len(stored.job_embedding) == 1024
    assert stored.embedding_model == BGE_M3_MODEL_NAME
    assert stored.embedding_preprocessing_version == RESUME_TEXT_PREPROCESSING_VERSION
    assert stored.parsed_at is not None and stored.parsing_error_message is None
    assert stored.is_criteria_verified is False
    assert stored.status == "DRAFT"
    assert len(job_skills) == 1
    assert job_skills[0].skill_id == skill.id
    assert job_skills[0].importance == "MANDATORY"
    assert job_skills[0].min_years_required == Decimal("2.0")
    assert taxonomy_count_after == taxonomy_count_before


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["delete", "revision"])
async def test_terminal_cas_blocks_delete_or_stale_revision_without_aggregate_replacement(
    postgres_job_worker: PostgreSQLJobWorkerHarness,
    race: str,
) -> None:
    harness = postgres_job_worker
    skill = await harness.setup_session.scalar(select(Skill).order_by(Skill.id.asc()))
    assert skill is not None
    job = await harness.create_job(f"Requirements\n{skill.name} required")
    harness.setup_session.add(
        JobSkill(
            job_id=job.id,
            skill_id=skill.id,
            importance="OPTIONAL",
            min_years_required=Decimal("7.0"),
        )
    )
    await harness.setup_session.commit()

    async def mutate_after_claim() -> None:
        values: dict[str, Any]
        if race == "delete":
            values = {"is_deleted": True, "deleted_at": datetime.now(UTC)}
        else:
            values = {"revision": 2, "parsing_status": "PENDING"}
        async with harness.session_factory() as session:
            await session.execute(
                update(JobDescription).where(JobDescription.id == job.id).values(**values)
            )
            await session.commit()

    outcome = await process_job_parse_task(
        job.id,
        1,
        session_factory=harness.session_factory,
        embedding_provider=MutatingEmbeddingProvider(mutate_after_claim),
    )

    async with harness.session_factory() as session:
        stored = await session.get(JobDescription, job.id)
        job_skills = (
            await session.scalars(select(JobSkill).where(JobSkill.job_id == job.id))
        ).all()
    assert outcome is JobParseTaskOutcome.DISCARDED
    assert stored is not None and stored.parsing_status != "PARSED"
    assert stored.job_embedding is None and stored.parsed_at is None
    assert len(job_skills) == 1
    assert job_skills[0].importance == "OPTIONAL"
    assert job_skills[0].min_years_required == Decimal("7.0")


@pytest.mark.asyncio
async def test_parser_and_embedding_failures_are_controlled(
    postgres_job_worker: PostgreSQLJobWorkerHarness,
) -> None:
    harness = postgres_job_worker
    parser_failure = await harness.create_job("--- !!!")
    embedding_failure = await harness.create_job("Requirements\nPython required")

    parser_outcome = await process_job_parse_task(
        parser_failure.id,
        1,
        session_factory=harness.session_factory,
        embedding_provider=FakeEmbeddingProvider(),
    )
    embedding_outcome = await process_job_parse_task(
        embedding_failure.id,
        1,
        session_factory=harness.session_factory,
        embedding_provider=FailingEmbeddingProvider(),
    )

    parser_row = await harness.load_job(parser_failure.id)
    embedding_row = await harness.load_job(embedding_failure.id)
    assert parser_outcome is JobParseTaskOutcome.FAILED
    assert embedding_outcome is JobParseTaskOutcome.FAILED
    assert parser_row.parsing_status == embedding_row.parsing_status == "FAILED"
    assert parser_row.revision == embedding_row.revision == 1
    assert parser_row.parsing_error_message == "Job parsing failed: NO_MEANINGFUL_TEXT"
    assert embedding_row.parsing_error_message == "Job embedding failed"
    assert "huggingface" not in embedding_row.parsing_error_message


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["delete", "revision"])
async def test_failure_cas_stale_or_deleted_is_discarded(
    postgres_job_worker: PostgreSQLJobWorkerHarness,
    race: str,
) -> None:
    harness = postgres_job_worker
    job = await harness.create_job()

    async def mutate_after_claim() -> None:
        values: dict[str, Any]
        if race == "delete":
            values = {"is_deleted": True, "deleted_at": datetime.now(UTC)}
        else:
            values = {"revision": 2, "parsing_status": "PENDING"}
        async with harness.session_factory() as session:
            await session.execute(
                update(JobDescription).where(JobDescription.id == job.id).values(**values)
            )
            await session.commit()

    outcome = await process_job_parse_task(
        job.id,
        1,
        session_factory=harness.session_factory,
        embedding_provider=MutatingFailingEmbeddingProvider(mutate_after_claim),
    )

    stored = await harness.load_job(job.id)
    assert outcome is JobParseTaskOutcome.DISCARDED
    assert stored.parsing_error_message is None
    assert stored.parsing_status != "FAILED"


@pytest.mark.asyncio
async def test_job_skill_insert_failure_rolls_back_parsed_payload_and_marks_failed(
    postgres_job_worker: PostgreSQLJobWorkerHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = postgres_job_worker
    skill = await harness.setup_session.scalar(select(Skill).order_by(Skill.id.asc()))
    assert skill is not None
    job = await harness.create_job()
    parsed = ParsedJobResult(
        normalized_text="valid normalized Job text",
        min_experience_years=Decimal("4.0"),
        education_requirement="Bachelor's degree",
        skills=(
            ParsedJobSkill(
                skill_id=skill.id,
                name=skill.name,
                normalized_name=skill.normalized_name,
                importance="MANDATORY",
                min_years_required=Decimal("1000.0"),
            ),
        ),
    )
    monkeypatch.setattr(job_parse_worker, "parse_job_description", lambda *args: parsed)

    outcome = await process_job_parse_task(
        job.id,
        1,
        session_factory=harness.session_factory,
        embedding_provider=FakeEmbeddingProvider(),
    )

    async with harness.session_factory() as session:
        stored = await session.get(JobDescription, job.id)
        child_count = await session.scalar(
            select(func.count()).select_from(JobSkill).where(JobSkill.job_id == job.id)
        )
    assert outcome is JobParseTaskOutcome.FAILED
    assert stored is not None and stored.parsing_status == "FAILED"
    assert stored.min_experience_years == Decimal("0.0")
    assert stored.education_requirement is None
    assert stored.job_embedding is None and stored.embedding_model is None
    assert stored.parsed_at is None
    assert stored.parsing_error_message == "Job parsed aggregate could not be persisted"
    assert child_count == 0


@pytest.mark.asyncio
async def test_unknown_parser_skill_never_mutates_taxonomy(
    postgres_job_worker: PostgreSQLJobWorkerHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = postgres_job_worker
    maximum_skill_id = await harness.setup_session.scalar(select(func.max(Skill.id))) or 0
    taxonomy_count_before = await harness.setup_session.scalar(
        select(func.count()).select_from(Skill)
    )
    job = await harness.create_job()
    parsed = ParsedJobResult(
        normalized_text="unknown skill",
        min_experience_years=Decimal("0.0"),
        education_requirement=None,
        skills=(
            ParsedJobSkill(
                skill_id=maximum_skill_id + 1,
                name="Unknown",
                normalized_name="unknown",
                importance="MANDATORY",
                min_years_required=Decimal("0.0"),
            ),
        ),
    )
    monkeypatch.setattr(job_parse_worker, "parse_job_description", lambda *args: parsed)

    outcome = await process_job_parse_task(
        job.id,
        1,
        session_factory=harness.session_factory,
        embedding_provider=FakeEmbeddingProvider(),
    )

    async with harness.session_factory() as session:
        child_count = await session.scalar(
            select(func.count()).select_from(JobSkill).where(JobSkill.job_id == job.id)
        )
        taxonomy_count_after = await session.scalar(select(func.count()).select_from(Skill))
    stored = await harness.load_job(job.id)
    assert outcome is JobParseTaskOutcome.FAILED
    assert stored.parsing_status == "FAILED"
    assert child_count == 0
    assert taxonomy_count_after == taxonomy_count_before


@pytest.mark.asyncio
async def test_two_independent_sessions_race_one_claim_wins() -> None:
    settings = integration_database_settings()
    engine = create_engine(settings)
    assert engine is not None
    user = make_user("race")
    job_id = uuid.uuid4()
    now = datetime.now(UTC)
    setup = AsyncSession(engine, expire_on_commit=False)
    cleanup = AsyncSession(engine, expire_on_commit=False)
    try:
        setup.add(user)
        await setup.commit()
        setup.add(
            JobDescription(
                id=job_id,
                recruiter_id=user.id,
                title="Claim Race",
                job_level="MID",
                raw_content="Requirements\nPython required",
                create_request_fingerprint=uuid.uuid4().hex * 2,
                revision=1,
                parsing_status="PENDING",
                created_at=now,
                updated_at=now,
            )
        )
        await setup.commit()

        async def claim() -> bool:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await claim_job_revision(
                    session,
                    job_id=job_id,
                    expected_revision=1,
                )

        results = await asyncio.gather(claim(), claim())
        assert sorted(results) == [False, True]
    finally:
        await setup.close()
        await cleanup.execute(delete(JobDescription).where(JobDescription.id == job_id))
        await cleanup.execute(delete(User).where(User.id == user.id))
        await cleanup.commit()
        await cleanup.close()
        await engine.dispose()
