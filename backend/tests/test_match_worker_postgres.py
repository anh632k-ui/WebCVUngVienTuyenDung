from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.ai.matching_engine import ALGORITHM_VERSION, compute_match
from app.core.config import Settings
from app.core.database import create_engine
from app.core.version_guard import claim_match_generation
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume, ResumeExperience
from app.models.skill import JobSkill, ResumeSkill, Skill
from app.models.user import User
from app.schemas.job_schema import JobCriteriaRequest
from app.services.job_service import update_job_criteria
from app.workers import match_worker
from app.workers.match_worker import MatchContextError, MatchTaskOutcome, process_match_task


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
class MatchGraph:
    match_id: uuid.UUID
    resume_id: uuid.UUID
    job_id: uuid.UUID
    skill_id: int


@dataclass
class PostgreSQLMatchHarness:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    user_id: uuid.UUID

    async def create_graph(
        self,
        *,
        generation: int = 1,
        resume_revision: int = 1,
        job_revision: int = 1,
        match_status: str = "PENDING",
        resume_deleted: bool = False,
        job_deleted: bool = False,
        include_job_skill: bool = True,
        resume_model: str = "BAAI/bge-m3",
        job_model: str = "BAAI/bge-m3",
        resume_embedding: list[float] | None = None,
        job_embedding: list[float] | None = None,
        min_experience_years: Decimal = Decimal("0.0"),
    ) -> MatchGraph:
        now = datetime.now(UTC)
        resume_id = uuid.uuid4()
        job_id = uuid.uuid4()
        match_id = uuid.uuid4()
        async with self.session_factory() as session:
            skill = await session.scalar(select(Skill).order_by(Skill.id.asc()))
            assert skill is not None
            session.add_all(
                [
                    Resume(
                        id=resume_id,
                        owner_user_id=self.user_id,
                        file_name="match-worker.pdf",
                        storage_key=f"match-worker/{resume_id}/source",
                        file_size=4,
                        mime_type="application/pdf",
                        create_request_fingerprint="a" * 64,
                        revision=resume_revision,
                        parsing_status="PARSED",
                        raw_text="Python engineer",
                        resume_embedding=(
                            resume_embedding if resume_embedding is not None else [0.25] * 1024
                        ),
                        embedding_model=resume_model,
                        embedding_preprocessing_version="text-v1",
                        is_deleted=resume_deleted,
                        deleted_at=now if resume_deleted else None,
                        created_at=now,
                        updated_at=now,
                        parsed_at=now,
                    ),
                    JobDescription(
                        id=job_id,
                        recruiter_id=self.user_id,
                        title="Backend Engineer",
                        job_level="MID",
                        raw_content="Python required",
                        create_request_fingerprint="b" * 64,
                        revision=job_revision,
                        min_experience_years=min_experience_years,
                        job_embedding=(
                            job_embedding if job_embedding is not None else [0.25] * 1024
                        ),
                        embedding_model=job_model,
                        embedding_preprocessing_version="text-v1",
                        parsing_status="PARSED",
                        is_criteria_verified=True,
                        status="DRAFT",
                        is_deleted=job_deleted,
                        deleted_at=now if job_deleted else None,
                        created_at=now,
                        updated_at=now,
                        parsed_at=now,
                    ),
                ]
            )
            await session.flush()
            session.add(
                ResumeSkill(
                    resume_id=resume_id,
                    skill_id=skill.id,
                    years_of_experience=Decimal("3.0"),
                )
            )
            if include_job_skill:
                session.add(
                    JobSkill(
                        job_id=job_id,
                        skill_id=skill.id,
                        importance="MANDATORY",
                        min_years_required=Decimal("0.0"),
                    )
                )
            session.add(
                ResumeExperience(
                    resume_id=resume_id,
                    company_name="Example",
                    job_title="Engineer",
                    start_date=date(2020, 1, 1),
                    end_date=None,
                    is_current=True,
                )
            )
            session.add(
                MatchResult(
                    id=match_id,
                    job_id=job_id,
                    resume_id=resume_id,
                    generation=generation,
                    resume_revision=resume_revision,
                    job_revision=job_revision,
                    algorithm_version=ALGORITHM_VERSION,
                    status=match_status,
                    error_message="prior failure" if match_status == "FAILED" else None,
                    overall_score=Decimal("100.00") if match_status == "COMPLETED" else None,
                    skill_score=Decimal("100.00") if match_status == "COMPLETED" else None,
                    semantic_score=Decimal("100.00") if match_status == "COMPLETED" else None,
                    experience_score=Decimal("100.00") if match_status == "COMPLETED" else None,
                    embedding_model="BAAI/bge-m3" if match_status == "COMPLETED" else None,
                    embedding_preprocessing_version=(
                        "text-v1" if match_status == "COMPLETED" else None
                    ),
                    created_at=now,
                    updated_at=now,
                    calculated_at=now if match_status == "COMPLETED" else None,
                )
            )
            await session.commit()
            return MatchGraph(match_id, resume_id, job_id, skill.id)

    async def load_match(self, match_id: uuid.UUID) -> MatchResult:
        async with self.session_factory() as session:
            result = await session.get(MatchResult, match_id)
            assert result is not None
            return result


@pytest_asyncio.fixture
async def postgres_match_worker() -> AsyncIterator[PostgreSQLMatchHarness]:
    engine = create_engine(integration_database_settings())
    assert engine is not None
    try:
        async with engine.connect() as connection:
            await connection.execute(select(1))
    except Exception:  # noqa: BLE001 - never expose database credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with factory() as session:
        session.add(
            User(
                id=user_id,
                email=f"match.worker.{user_id.hex}@example.com",
                password_hash="not-used-by-worker-integration",
                full_name="Match Worker Integration",
                role="HR",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    try:
        yield PostgreSQLMatchHarness(engine, factory, user_id)
    finally:
        async with factory() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
            remaining = await session.scalar(
                select(func.count()).select_from(User).where(User.id == user_id)
            )
        await engine.dispose()
        assert remaining == 0


async def claim(harness: PostgreSQLMatchHarness, graph: MatchGraph, **changes: Any) -> bool:
    async with harness.session_factory() as session:
        return await claim_match_generation(
            session,
            match_id=graph.match_id,
            expected_generation=changes.get("generation", 1),
            expected_resume_revision=changes.get("resume_revision", 1),
            expected_job_revision=changes.get("job_revision", 1),
            algorithm_version=changes.get("algorithm_version", ALGORITHM_VERSION),
        )


@pytest.mark.asyncio
async def test_claim_exact_guards_status_snapshots_resources_and_duplicate_delivery(
    postgres_match_worker: PostgreSQLMatchHarness,
) -> None:
    harness = postgres_match_worker
    exact = await harness.create_graph()
    stale_generation = await harness.create_graph(generation=2)
    stale_resume_snapshot = await harness.create_graph(resume_revision=2)
    stale_job_snapshot = await harness.create_graph(job_revision=2)
    deleted_resume = await harness.create_graph(resume_deleted=True)
    deleted_job = await harness.create_graph(job_deleted=True)
    changed_resume = await harness.create_graph()
    changed_job = await harness.create_graph()
    processing = await harness.create_graph(match_status="PROCESSING")
    completed = await harness.create_graph(match_status="COMPLETED")
    failed = await harness.create_graph(match_status="FAILED")
    async with harness.session_factory() as session:
        await session.execute(
            update(Resume).where(Resume.id == changed_resume.resume_id).values(revision=2)
        )
        await session.execute(
            update(JobDescription).where(JobDescription.id == changed_job.job_id).values(revision=2)
        )
        await session.commit()

    assert await claim(harness, exact) is True
    assert await claim(harness, exact) is False
    assert await claim(harness, stale_generation) is False
    assert await claim(harness, stale_resume_snapshot) is False
    assert await claim(harness, stale_job_snapshot) is False
    assert await claim(harness, deleted_resume) is False
    assert await claim(harness, deleted_job) is False
    assert await claim(harness, changed_resume) is False
    assert await claim(harness, changed_job) is False
    assert await claim(harness, processing) is False
    assert await claim(harness, completed) is False
    assert await claim(harness, failed) is False


@pytest.mark.asyncio
async def test_two_independent_sessions_race_and_only_one_claims(
    postgres_match_worker: PostgreSQLMatchHarness,
) -> None:
    harness = postgres_match_worker
    graph = await harness.create_graph()

    results = await asyncio.gather(claim(harness, graph), claim(harness, graph))

    assert sorted(results) == [False, True]
    assert (await harness.load_match(graph.match_id)).status == "PROCESSING"
    async with harness.session_factory() as session:
        assert await session.scalar(select(1)) == 1


@pytest.mark.asyncio
async def test_real_engine_success_persists_complete_payload_without_mutating_inputs(
    postgres_match_worker: PostgreSQLMatchHarness,
) -> None:
    harness = postgres_match_worker
    graph = await harness.create_graph()

    outcome = await process_match_task(
        graph.match_id,
        1,
        1,
        1,
        ALGORITHM_VERSION,
        session_factory=harness.session_factory,
        date_provider=lambda: date(2026, 10, 7),
    )

    stored = await harness.load_match(graph.match_id)
    async with harness.session_factory() as session:
        resume = await session.get(Resume, graph.resume_id)
        job = await session.get(JobDescription, graph.job_id)
        resume_skill = await session.scalar(
            select(ResumeSkill).where(ResumeSkill.resume_id == graph.resume_id)
        )
        job_skill = await session.scalar(select(JobSkill).where(JobSkill.job_id == graph.job_id))
        experience = await session.scalar(
            select(ResumeExperience).where(ResumeExperience.resume_id == graph.resume_id)
        )
        count = await session.scalar(
            select(func.count())
            .select_from(MatchResult)
            .where(MatchResult.job_id == graph.job_id, MatchResult.resume_id == graph.resume_id)
        )
    assert outcome is MatchTaskOutcome.COMPLETED
    assert stored.status == "COMPLETED"
    assert stored.overall_score == stored.skill_score == stored.semantic_score == Decimal("100.00")
    assert stored.experience_score == Decimal("100.00")
    assert stored.matched_skills == [
        {
            "skill_id": graph.skill_id,
            "name": stored.matched_skills[0]["name"],
            "importance": "MANDATORY",
            "status": "MATCHED",
            "candidate_years": 3.0,
            "required_years": 0.0,
            "severity": None,
        }
    ]
    assert stored.missing_skills == [] and stored.gap_analysis_summary is None
    assert stored.embedding_model == "BAAI/bge-m3"
    assert stored.embedding_preprocessing_version == "text-v1"
    assert stored.calculated_at is not None and stored.updated_at == stored.calculated_at
    assert stored.generation == stored.resume_revision == stored.job_revision == 1
    assert resume is not None and resume.revision == 1
    assert job is not None and job.revision == 1
    assert resume_skill is not None and resume_skill.years_of_experience == Decimal("3.0")
    assert job_skill is not None and job_skill.importance == "MANDATORY"
    assert job_skill.min_years_required == Decimal("0.0")
    assert experience is not None and experience.start_date == date(2020, 1, 1)
    assert count == 1


@pytest.mark.asyncio
async def test_current_experience_uses_the_single_injected_as_of_date(
    postgres_match_worker: PostgreSQLMatchHarness,
) -> None:
    harness = postgres_match_worker
    graph = await harness.create_graph(min_experience_years=Decimal("10.0"))
    calls = 0

    def date_provider() -> date:
        nonlocal calls
        calls += 1
        return date(2025, 1, 1)

    outcome = await process_match_task(
        graph.match_id,
        1,
        1,
        1,
        ALGORITHM_VERSION,
        session_factory=harness.session_factory,
        date_provider=date_provider,
    )
    stored = await harness.load_match(graph.match_id)

    assert outcome is MatchTaskOutcome.COMPLETED
    assert calls == 1
    assert stored.experience_score == Decimal("50.02")
    assert stored.overall_score == Decimal("90.00")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "target"),
    [
        ({"generation": 2, "status": "PENDING"}, "match"),
        ({"revision": 2}, "resume"),
        ({"revision": 2}, "job"),
        ({"is_deleted": True, "deleted_at": datetime.now(UTC)}, "resume"),
        ({"is_deleted": True, "deleted_at": datetime.now(UTC)}, "job"),
    ],
)
async def test_terminal_success_discards_every_stale_or_deleted_race(
    postgres_match_worker: PostgreSQLMatchHarness,
    monkeypatch: pytest.MonkeyPatch,
    mutation: dict[str, Any],
    target: str,
) -> None:
    harness = postgres_match_worker
    graph = await harness.create_graph()
    original_load = match_worker._load_computation_context

    async def load_then_mutate(*args: Any, **kwargs: Any) -> Any:
        context = await original_load(*args, **kwargs)
        resource_id = {
            "match": graph.match_id,
            "resume": graph.resume_id,
            "job": graph.job_id,
        }[target]
        async with harness.session_factory() as session:
            if target == "match":
                statement = (
                    update(MatchResult).where(MatchResult.id == resource_id).values(**mutation)
                )
            elif target == "resume":
                statement = update(Resume).where(Resume.id == resource_id).values(**mutation)
            else:
                statement = (
                    update(JobDescription)
                    .where(JobDescription.id == resource_id)
                    .values(**mutation)
                )
            await session.execute(statement)
            await session.commit()
        return context

    monkeypatch.setattr(match_worker, "_load_computation_context", load_then_mutate)
    outcome = await process_match_task(
        graph.match_id, 1, 1, 1, ALGORITHM_VERSION, session_factory=harness.session_factory
    )
    stored = await harness.load_match(graph.match_id)

    assert outcome is MatchTaskOutcome.DISCARDED
    assert stored.status != "COMPLETED"
    assert stored.overall_score is None and stored.matched_skills == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["no-skills", "provenance", "zero-vector"])
async def test_invalid_persisted_context_transitions_to_safe_cleared_failed(
    postgres_match_worker: PostgreSQLMatchHarness,
    failure: str,
) -> None:
    harness = postgres_match_worker
    graph = await harness.create_graph(
        include_job_skill=failure != "no-skills",
        job_model="different-model" if failure == "provenance" else "BAAI/bge-m3",
        resume_embedding=[0.0] * 1024 if failure == "zero-vector" else None,
    )

    outcome = await process_match_task(
        graph.match_id, 1, 1, 1, ALGORITHM_VERSION, session_factory=harness.session_factory
    )
    stored = await harness.load_match(graph.match_id)

    assert outcome is MatchTaskOutcome.FAILED
    assert stored.status == "FAILED" and stored.error_message
    assert len(stored.error_message) <= match_worker.MAX_MATCH_WORKER_ERROR_MESSAGE_LENGTH
    assert stored.overall_score is None and stored.skill_score is None
    assert stored.semantic_score is None and stored.experience_score is None
    assert stored.matched_skills == [] and stored.missing_skills == []
    assert stored.embedding_model is None and stored.calculated_at is None
    assert stored.generation == stored.resume_revision == stored.job_revision == 1


@pytest.mark.asyncio
async def test_failure_terminal_stale_guard_discards_instead_of_marking_failed(
    postgres_match_worker: PostgreSQLMatchHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = postgres_match_worker
    graph = await harness.create_graph()

    async def stale_context(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        async with harness.session_factory() as session:
            await session.execute(
                update(Resume).where(Resume.id == graph.resume_id).values(revision=2)
            )
            await session.commit()
        raise MatchContextError("private details")

    monkeypatch.setattr(match_worker, "_load_computation_context", stale_context)
    outcome = await process_match_task(
        graph.match_id, 1, 1, 1, ALGORITHM_VERSION, session_factory=harness.session_factory
    )
    stored = await harness.load_match(graph.match_id)

    assert outcome is MatchTaskOutcome.DISCARDED
    assert stored.status == "PROCESSING" and stored.error_message is None


@pytest.mark.asyncio
async def test_terminal_resource_lock_observes_concurrent_soft_delete_before_completion(
    postgres_match_worker: PostgreSQLMatchHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = postgres_match_worker
    graph = await harness.create_graph()
    assert await claim(harness, graph)
    context = await match_worker._load_computation_context(
        harness.session_factory,
        match_id=graph.match_id,
        expected_generation=1,
        expected_resume_revision=1,
        expected_job_revision=1,
        algorithm_version=ALGORITHM_VERSION,
    )
    assert context is not None
    computed = compute_match(
        candidate_skills=context.candidate_skills,
        job_skills=context.job_skills,
        candidate_experiences=context.candidate_experiences,
        min_experience_years=context.min_experience_years,
        weights=context.weights,
        embeddings=context.embeddings,
        as_of_date=date(2026, 10, 7),
    )
    terminal_reached_resource_lock = asyncio.Event()
    original_lock = match_worker._lock_current_resources

    async def observed_lock(*args: Any, **kwargs: Any) -> bool:
        terminal_reached_resource_lock.set()
        return await original_lock(*args, **kwargs)

    monkeypatch.setattr(match_worker, "_lock_current_resources", observed_lock)

    updater = harness.session_factory()
    try:
        resume = await updater.scalar(
            select(Resume).where(Resume.id == graph.resume_id).with_for_update()
        )
        assert resume is not None
        resume.is_deleted = True
        resume.deleted_at = datetime.now(UTC)
        await updater.flush()

        terminal = asyncio.create_task(
            match_worker._persist_success(
                harness.session_factory,
                match_id=graph.match_id,
                expected_generation=1,
                expected_resume_revision=1,
                expected_job_revision=1,
                algorithm_version=ALGORITHM_VERSION,
                context=context,
                computed=computed,
            )
        )
        await terminal_reached_resource_lock.wait()
        assert not terminal.done()
        await updater.commit()
        persisted = await terminal
    finally:
        await updater.close()

    assert persisted is False
    stored = await harness.load_match(graph.match_id)
    assert stored.status == "PROCESSING" and stored.overall_score is None


async def wait_for_database_blocker(
    observer: AsyncSession, *, waiter_pid: int, blocker_pid: int
) -> None:
    # Observe an actual PostgreSQL lock wait, rather than infer timing from sleep
    # or an event fired before the SQL statement has reached the database.
    async with asyncio.timeout(5):
        while True:
            blockers = await observer.scalar(
                text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter_pid}
            )
            if blocker_pid in blockers:
                return


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["COMPLETED", "FAILED"])
@pytest.mark.parametrize("first_lock", ["criteria", "terminal"])
async def test_terminal_and_criteria_update_serialize_without_deadlock_or_stale_payload(
    postgres_match_worker: PostgreSQLMatchHarness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    terminal_status: str,
    first_lock: str,
) -> None:
    harness = postgres_match_worker
    graph = await harness.create_graph()
    assert await claim(harness, graph)
    context = await match_worker._load_computation_context(
        harness.session_factory,
        match_id=graph.match_id,
        expected_generation=1,
        expected_resume_revision=1,
        expected_job_revision=1,
        algorithm_version=ALGORITHM_VERSION,
    )
    assert context is not None
    computed = compute_match(
        candidate_skills=context.candidate_skills,
        job_skills=context.job_skills,
        candidate_experiences=context.candidate_experiences,
        min_experience_years=context.min_experience_years,
        weights=context.weights,
        embeddings=context.embeddings,
        as_of_date=date(2026, 10, 7),
    )
    payload = JobCriteriaRequest.model_validate(
        {
            "min_experience_years": 4.0,
            "skills": [
                {"skill_id": graph.skill_id, "importance": "OPTIONAL", "min_years_required": 2.0}
            ],
        }
    )
    job_locked = asyncio.Event()
    release_terminal = asyncio.Event()
    tasks: list[asyncio.Task[Any]] = []
    async with (
        harness.session_factory() as criteria_session,
        harness.session_factory() as terminal_session,
        harness.session_factory() as observer,
    ):
        actor = await criteria_session.get(User, harness.user_id)
        assert actor is not None
        criteria_pid = await criteria_session.scalar(text("SELECT pg_backend_pid()"))
        terminal_pid = await terminal_session.scalar(text("SELECT pg_backend_pid()"))
        assert criteria_pid is not None and terminal_pid is not None
        assert criteria_pid != terminal_pid
        # Bound database waits too, so a regression cannot hang fixture cleanup.
        for session in (criteria_session, terminal_session):
            await session.execute(text("SET LOCAL lock_timeout = '4s'"))

        async def terminal_write() -> bool:
            guard: match_worker.MatchTaskGuard = {
                "match_id": graph.match_id,
                "expected_generation": 1,
                "expected_resume_revision": 1,
                "expected_job_revision": 1,
                "algorithm_version": ALGORITHM_VERSION,
            }
            if terminal_status == "FAILED":
                outcome = await match_worker._mark_failed(
                    lambda: terminal_session, **guard, message="Controlled computation failure"
                )
                return outcome is MatchTaskOutcome.FAILED
            return await match_worker._persist_success(
                lambda: terminal_session, **guard, context=context, computed=computed
            )

        async def criteria_update() -> None:
            await update_job_criteria(
                criteria_session, current_user=actor, job_id=graph.job_id, payload=payload
            )

        try:
            async with asyncio.timeout(10):
                if first_lock == "criteria":
                    # Keep the real criteria transaction's Job lock while the
                    # terminal reaches the database. With Resume-first locking,
                    # criteria invalidation would now deadlock on that Resume.
                    await criteria_session.scalar(
                        select(JobDescription)
                        .where(JobDescription.id == graph.job_id)
                        .with_for_update()
                    )
                    terminal_task = asyncio.create_task(terminal_write())
                    tasks.append(terminal_task)
                    await wait_for_database_blocker(
                        observer, waiter_pid=terminal_pid, blocker_pid=criteria_pid
                    )
                    await criteria_update()
                    assert await terminal_task is False
                else:
                    original_scalar = terminal_session.scalar

                    async def gate_after_job_lock(statement: Any, *args: Any, **kwargs: Any) -> Any:
                        result = await original_scalar(statement, *args, **kwargs)
                        descriptions = getattr(statement, "column_descriptions", ())
                        if descriptions and descriptions[0].get("entity") is JobDescription:
                            job_locked.set()
                            await release_terminal.wait()
                        return result

                    monkeypatch.setattr(terminal_session, "scalar", gate_after_job_lock)
                    terminal_task = asyncio.create_task(terminal_write())
                    tasks.append(terminal_task)
                    await job_locked.wait()
                    criteria_task = asyncio.create_task(criteria_update())
                    tasks.append(criteria_task)
                    await wait_for_database_blocker(
                        observer, waiter_pid=criteria_pid, blocker_pid=terminal_pid
                    )
                    release_terminal.set()
                    assert await terminal_task is True
                    await criteria_task
        finally:
            release_terminal.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    assert "match_failure_cas_error" not in caplog.text
    stored = await harness.load_match(graph.match_id)
    assert stored.generation == 2
    assert stored.resume_revision == 1 and stored.job_revision == 2
    assert stored.status == "PENDING"
    assert stored.overall_score is None and stored.skill_score is None
    assert stored.semantic_score is None and stored.experience_score is None
    assert stored.matched_skills == [] and stored.missing_skills == []
    assert stored.gap_analysis_summary is None and stored.error_message is None
    assert stored.embedding_model is None and stored.embedding_preprocessing_version is None
    assert stored.calculated_at is None
    async with harness.session_factory() as session:
        job = await session.get(JobDescription, graph.job_id)
        resume = await session.get(Resume, graph.resume_id)
        criterion = await session.scalar(select(JobSkill).where(JobSkill.job_id == graph.job_id))
        assert job is not None and job.revision == 2
        assert job.min_experience_years == Decimal("4.0") and job.is_criteria_verified
        assert resume is not None and resume.revision == 1
        assert criterion is not None and criterion.importance == "OPTIONAL"
        assert criterion.min_years_required == Decimal("2.0")
