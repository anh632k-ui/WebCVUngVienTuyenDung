from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from test_job_update_postgres import (
    CommitGateSession,
    ControlledEmbeddingProvider,
    ExistingSessionFactory,
    FailingEmbeddingProvider,
    GatedEmbeddingProvider,
    InspectingCommitGateSession,
    JobUpdateHarness,
    RecordingDispatcher,
    RecordingMatchDispatcher,
    TerminalCommitGateFactory,
    TrackedTerminalFactory,
    job_update_postgres,
    load_actor,
    make_match,
    terminal_values,
    wait_for_blocker,
    wait_graph_reaches,
)

from app.core.version_guard import claim_match_generation
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import CandidateProfile, Resume
from app.models.skill import JobSkill
from app.schemas.job_schema import (
    JobCriteriaRequest,
    JobUpdateRequest,
    JobWeightsRequest,
)
from app.schemas.match_schema import MatchCalculateRequest
from app.schemas.resume_schema import ResumeParsedDataUpdate
from app.services.job_service import (
    soft_delete_job,
    update_job,
    update_job_criteria,
    update_job_weights,
)
from app.services.match_service import calculate_matches
from app.services.resume_service import soft_delete_resume, update_resume_parsed_data
from app.workers.job_parse_worker import JobParseTaskOutcome, process_job_parse_task
from app.workers.match_worker import MatchTaskOutcome, _terminal_update, process_match_task

# Importing the fixture is intentional: these tests use the established cross-feature
# PostgreSQL topology but every operation under test invokes UC18's weights service.
_job_update_fixture = job_update_postgres


class JobLockGateSession(AsyncSession):
    """Pause after the UC18 Job locking read, before the readiness decision."""

    def __init__(
        self,
        *args: Any,
        entered: asyncio.Event,
        release: asyncio.Event,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.entered = entered
        self.release = release
        self.pid: int | None = None

    async def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        result = await super().scalar(statement, *args, **kwargs)
        sql = str(statement)
        if "job_descriptions" in sql and "FOR UPDATE" in sql:
            self.pid = await super().scalar(text("SELECT pg_backend_pid()"))
            self.entered.set()
            await self.release.wait()
        return result


def weights(*, skill: float = 0.2) -> JobWeightsRequest:
    semantic = 0.7 if skill == 0.2 else 0.2
    experience = 0.1 if skill == 0.2 else 0.2
    return JobWeightsRequest.model_validate(
        {
            "w_skill": skill,
            "w_semantic": semantic,
            "w_experience": experience,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["success", "failed"])
async def test_parser_terminal_first_serializes_with_weights_at_actual_commit_boundary(
    job_update_postgres: JobUpdateHarness,
    terminal: str,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["pending"]
    match_id = uuid.uuid4()
    async with harness.factory() as setup:
        job = await setup.get(JobDescription, job_id)
        resume = await setup.get(Resume, harness.resumes[0])
        assert job is not None and resume is not None
        match = make_match(job, resume, 70, "FAILED")
        match.id = match_id
        setup.add(match)
        await setup.commit()
        initial_generation = match.generation

    async def inspect_terminal(session: AsyncSession) -> tuple[Any, ...]:
        row = (
            await session.execute(
                select(
                    JobDescription.parsing_status,
                    JobDescription.parsing_error_message,
                    JobDescription.job_embedding.is_not(None),
                    JobDescription.parsed_at.is_not(None),
                    JobDescription.revision,
                ).where(JobDescription.id == job_id)
            )
        ).one()
        return tuple(row)

    entered = asyncio.Event()
    release = asyncio.Event()
    bind = harness.factory.kw["bind"]
    db_errors: list[BaseException] = []

    def record_db_error(exception_context: Any) -> None:
        db_errors.append(exception_context.original_exception)

    event.listen(bind.sync_engine, "handle_error", record_db_error)
    terminal_factory = TerminalCommitGateFactory(
        bind,
        terminal_call=3,
        entered=entered,
        release=release,
        inspect=inspect_terminal,
    )
    weights_session = harness.factory()
    observer = harness.factory()
    provider = (
        ControlledEmbeddingProvider() if terminal == "success" else FailingEmbeddingProvider()
    )
    worker = asyncio.create_task(
        process_job_parse_task(
            job_id,
            7,
            session_factory=terminal_factory,
            embedding_provider=provider,
        )
    )
    weights_task: asyncio.Task[Any] | None = None
    try:
        actor = await load_actor(weights_session, harness.users["hr"])
        weights_pid = await weights_session.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(weights_pid, int)
        await asyncio.wait_for(entered.wait(), timeout=10)
        terminal_session = terminal_factory.terminal_session
        assert terminal_session is not None and terminal_session.pid is not None
        expected_snapshot = (
            ("PARSED", None, True, True, 7)
            if terminal == "success"
            else ("FAILED", "Job embedding failed", False, False, 7)
        )
        assert terminal_session.snapshot == expected_snapshot
        weights_task = asyncio.create_task(
            update_job_weights(
                weights_session,
                current_user=actor,
                job_id=job_id,
                payload=weights(),
                dispatcher=RecordingMatchDispatcher(),
            )
        )
        assert await wait_for_blocker(
            observer,
            waiter_pid=weights_pid,
            blocker_pid=terminal_session.pid,
        ) == (terminal_session.pid,)
        release.set()
        outcome = await asyncio.wait_for(worker, timeout=10)
        if terminal == "success":
            updated = await asyncio.wait_for(weights_task, timeout=10)
            assert updated.revision == 8
        else:
            result = await asyncio.gather(weights_task, return_exceptions=True)
            assert len(result) == 1
            assert getattr(result[0], "status_code", None) == 422
    finally:
        release.set()
        tasks = [worker] + ([] if weights_task is None else [weights_task])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await weights_session.close()
        await observer.close()
        event.remove(bind.sync_engine, "handle_error", record_db_error)

    assert outcome is (
        JobParseTaskOutcome.PARSED if terminal == "success" else JobParseTaskOutcome.FAILED
    )
    assert db_errors == []
    async with harness.factory() as verify:
        job = await verify.get(JobDescription, job_id)
        match = await verify.get(MatchResult, match_id)
        resume = await verify.get(Resume, harness.resumes[0])
        assert job is not None and match is not None and resume is not None
        if terminal == "success":
            assert (job.revision, job.parsing_status) == (8, "PARSED")
            assert (match.generation, match.resume_revision, match.job_revision, match.status) == (
                initial_generation + 1,
                resume.revision,
                8,
                "PENDING",
            )
        else:
            assert (job.revision, job.parsing_status) == (7, "FAILED")
            assert (match.generation, match.job_revision, match.status) == (
                initial_generation,
                7,
                "FAILED",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["success", "failed"])
async def test_weights_attempt_first_rejects_processing_then_parser_terminal_completes(
    job_update_postgres: JobUpdateHarness,
    terminal: str,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["pending"]
    bind = harness.factory.kw["bind"]
    provider = GatedEmbeddingProvider(fail=terminal == "failed")
    terminal_factory = TrackedTerminalFactory(bind, terminal_call=3)
    worker = asyncio.create_task(
        process_job_parse_task(
            job_id,
            7,
            session_factory=terminal_factory,
            embedding_provider=provider,
        )
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    weights_session = JobLockGateSession(
        bind=bind,
        expire_on_commit=False,
        entered=entered,
        release=release,
    )
    observer = harness.factory()
    db_errors: list[BaseException] = []

    def record_db_error(exception_context: Any) -> None:
        db_errors.append(exception_context.original_exception)

    event.listen(bind.sync_engine, "handle_error", record_db_error)
    weights_task: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        async with harness.factory() as claimed:
            row = await claimed.get(JobDescription, job_id)
            assert row is not None and (row.revision, row.parsing_status) == (7, "PROCESSING")
        actor = await load_actor(weights_session, harness.users["hr"])
        weights_task = asyncio.create_task(
            update_job_weights(
                weights_session,
                current_user=actor,
                job_id=job_id,
                payload=weights(),
                dispatcher=RecordingMatchDispatcher(),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert weights_session.pid is not None
        provider.release.set()
        await asyncio.wait_for(terminal_factory.entered.wait(), timeout=5)
        assert terminal_factory.pid is not None
        assert await wait_for_blocker(
            observer,
            waiter_pid=terminal_factory.pid,
            blocker_pid=weights_session.pid,
        ) == (weights_session.pid,)
        release.set()
        result = await asyncio.gather(weights_task, return_exceptions=True)
        assert len(result) == 1 and getattr(result[0], "status_code", None) == 422
        outcome = await asyncio.wait_for(worker, timeout=10)
    finally:
        provider.release.set()
        release.set()
        tasks = [worker] + ([] if weights_task is None else [weights_task])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await weights_session.close()
        await observer.close()
        event.remove(bind.sync_engine, "handle_error", record_db_error)

    assert outcome is (
        JobParseTaskOutcome.PARSED if terminal == "success" else JobParseTaskOutcome.FAILED
    )
    assert db_errors == []
    async with harness.factory() as verify:
        row = await verify.get(JobDescription, job_id)
        assert row is not None
        assert (row.revision, row.parsing_status) == (
            7,
            "PARSED" if terminal == "success" else "FAILED",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("competitor", ["raw", "metadata", "criteria"])
@pytest.mark.parametrize("weights_first", [True, False], ids=["weights-first", "mutator-first"])
async def test_job_mutator_and_weights_serialize_from_current_locked_state(
    job_update_postgres: JobUpdateHarness,
    competitor: str,
    weights_first: bool,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    bind = harness.factory.kw["bind"]
    entered = asyncio.Event()
    release = asyncio.Event()
    first = CommitGateSession(
        bind=bind,
        expire_on_commit=False,
        commit_entered=entered,
        commit_release=release,
    )
    second = harness.factory()
    observer = harness.factory()
    first_task: asyncio.Task[Any] | None = None
    second_task: asyncio.Task[Any] | None = None
    criteria = JobCriteriaRequest.model_validate(
        {
            "min_experience_years": 6,
            "skills": [
                {
                    "skill_id": harness.skill_id,
                    "importance": "OPTIONAL",
                    "min_years_required": 1,
                }
            ],
        }
    )

    async def run_weights(session: AsyncSession, actor: Any) -> Any:
        return await update_job_weights(
            session,
            current_user=actor,
            job_id=job_id,
            payload=weights(),
            dispatcher=RecordingMatchDispatcher(),
        )

    async def run_mutator(session: AsyncSession, actor: Any) -> Any:
        if competitor == "raw":
            return await update_job(
                session,
                current_user=actor,
                job_id=job_id,
                payload=JobUpdateRequest(raw_content="raw mutation around weights"),
                dispatcher=RecordingDispatcher(),
            )
        if competitor == "metadata":
            return await update_job(
                session,
                current_user=actor,
                job_id=job_id,
                payload=JobUpdateRequest(title="metadata around weights"),
                dispatcher=RecordingDispatcher(),
            )
        return await update_job_criteria(
            session,
            current_user=actor,
            job_id=job_id,
            payload=criteria,
        )

    try:
        first_actor = await load_actor(first, harness.users["hr"])
        second_actor = await load_actor(second, harness.users["hr"])
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(first_pid, int) and isinstance(second_pid, int)
        first_task = asyncio.create_task(
            run_weights(first, first_actor) if weights_first else run_mutator(first, first_actor)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        second_task = asyncio.create_task(
            run_mutator(second, second_actor)
            if weights_first
            else run_weights(second, second_actor)
        )
        assert await wait_for_blocker(observer, waiter_pid=second_pid, blocker_pid=first_pid) == (
            first_pid,
        )
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(first_task, second_task, return_exceptions=True), timeout=10
        )
    finally:
        release.set()
        tasks = [task for task in (first_task, second_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await first.close()
        await second.close()
        await observer.close()

    failures = [result for result in results if isinstance(result, BaseException)]
    if competitor == "raw" and not weights_first:
        assert len(failures) == 1 and getattr(failures[0], "status_code", None) == 422
    else:
        assert failures == []

    async with harness.factory() as verify:
        job = await verify.get(JobDescription, job_id)
        rows = (await verify.scalars(select(MatchResult).where(MatchResult.job_id == job_id))).all()
        assert job is not None
        if competitor == "raw":
            assert job.revision == (9 if weights_first else 8)
            assert job.parsing_status == "PENDING"
            if weights_first:
                assert (job.w_skill, job.w_semantic, job.w_experience) == (
                    Decimal("0.200"),
                    Decimal("0.700"),
                    Decimal("0.100"),
                )
                assert sorted(row.generation for row in rows) == [12, 13, 14, 15, 16]
            else:
                assert sorted(row.generation for row in rows) == [11, 12, 13, 14, 15]
        elif competitor == "criteria":
            assert job.revision == 9
            assert all(row.job_revision == 9 and row.status == "PENDING" for row in rows)
            assert sorted(row.generation for row in rows) == [12, 13, 14, 15, 16]
        else:
            assert job.revision == 8
            assert job.title == "metadata around weights"
            assert (job.w_skill, job.w_semantic, job.w_experience) == (
                Decimal("0.200"),
                Decimal("0.700"),
                Decimal("0.100"),
            )
            assert all(row.job_revision == 8 and row.status == "PENDING" for row in rows)
            assert sorted(row.generation for row in rows) == [11, 12, 13, 14, 15]


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger_first", [True, False], ids=["trigger-first", "weights-first"])
async def test_matching_trigger_existing_and_new_pair_serializes_with_weights(
    job_update_postgres: JobUpdateHarness,
    trigger_first: bool,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    existing_resume, new_resume = harness.resumes[:2]
    async with harness.factory() as setup:
        await setup.execute(
            delete(MatchResult).where(
                MatchResult.job_id == job_id,
                MatchResult.resume_id == new_resume,
            )
        )
        existing = await setup.scalar(
            select(MatchResult).where(
                MatchResult.job_id == job_id,
                MatchResult.resume_id == existing_resume,
            )
        )
        assert existing is not None
        existing_id = existing.id
        existing_generation = existing.generation
        await setup.commit()

    bind = harness.factory.kw["bind"]
    entered = asyncio.Event()
    release = asyncio.Event()
    first = CommitGateSession(
        bind=bind,
        expire_on_commit=False,
        commit_entered=entered,
        commit_release=release,
    )
    second = harness.factory()
    observer = harness.factory()
    first_task: asyncio.Task[Any] | None = None
    second_task: asyncio.Task[Any] | None = None

    async def run_weights(session: AsyncSession, actor: Any) -> Any:
        return await update_job_weights(
            session,
            current_user=actor,
            job_id=job_id,
            payload=weights(),
            dispatcher=RecordingMatchDispatcher(),
        )

    async def run_trigger(session: AsyncSession, actor: Any) -> Any:
        return await calculate_matches(
            session,
            current_user=actor,
            payload=MatchCalculateRequest(
                job_id=job_id,
                resume_ids=[new_resume, existing_resume],
            ),
            dispatcher=RecordingMatchDispatcher(),
        )

    try:
        first_actor = await load_actor(first, harness.users["hr"])
        second_actor = await load_actor(second, harness.users["hr"])
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(first_pid, int) and isinstance(second_pid, int)
        first_task = asyncio.create_task(
            run_trigger(first, first_actor) if trigger_first else run_weights(first, first_actor)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        if not trigger_first:
            async with harness.factory() as inspect:
                assert (
                    await inspect.scalar(
                        select(MatchResult.id).where(
                            MatchResult.job_id == job_id,
                            MatchResult.resume_id == new_resume,
                        )
                    )
                    is None
                )
        second_task = asyncio.create_task(
            run_weights(second, second_actor)
            if trigger_first
            else run_trigger(second, second_actor)
        )
        assert await wait_for_blocker(observer, waiter_pid=second_pid, blocker_pid=first_pid) == (
            first_pid,
        )
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(first_task, second_task, return_exceptions=True), timeout=10
        )
        assert not [result for result in results if isinstance(result, BaseException)]
    finally:
        release.set()
        tasks = [task for task in (first_task, second_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await first.close()
        await second.close()
        await observer.close()

    async with harness.factory() as verify:
        rows = (
            await verify.scalars(
                select(MatchResult).where(
                    MatchResult.job_id == job_id,
                    MatchResult.resume_id.in_((existing_resume, new_resume)),
                )
            )
        ).all()
        assert len(rows) == 2
        by_resume = {row.resume_id: row for row in rows}
        existing = by_resume[existing_resume]
        created = by_resume[new_resume]
        assert existing.id == existing_id
        assert existing.generation == existing_generation + 2
        assert created.generation == (2 if trigger_first else 1)
        assert all(
            row.job_revision == 8
            and row.status == "PENDING"
            and row.overall_score is None
            and row.error_message is None
            for row in rows
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["COMPLETED", "FAILED"])
@pytest.mark.parametrize("terminal_first", [True, False], ids=["terminal-first", "weights-first"])
async def test_claimed_match_terminal_and_weights_serialize_at_actual_cas_boundary(
    job_update_postgres: JobUpdateHarness,
    status: str,
    terminal_first: bool,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    match_id = harness.matches[0]
    async with harness.factory() as load:
        before = await load.get(MatchResult, match_id)
        assert before is not None
        task_payload = (
            before.id,
            before.generation,
            before.resume_revision,
            before.job_revision,
            before.algorithm_version,
        )
        original_generation = before.generation
    async with harness.factory() as claim_session:
        assert await claim_match_generation(
            claim_session,
            match_id=task_payload[0],
            expected_generation=task_payload[1],
            expected_resume_revision=task_payload[2],
            expected_job_revision=task_payload[3],
            algorithm_version=task_payload[4],
        )

    values = terminal_values(status)
    bind = harness.factory.kw["bind"]
    if terminal_first:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def inspect_terminal(session: AsyncSession) -> tuple[Any, ...]:
            row = (
                await session.execute(
                    select(
                        MatchResult.status,
                        MatchResult.overall_score,
                        MatchResult.matched_skills,
                        MatchResult.error_message,
                        MatchResult.embedding_model,
                        MatchResult.calculated_at.is_not(None),
                    ).where(MatchResult.id == match_id)
                )
            ).one()
            return tuple(row)

        terminal_session = InspectingCommitGateSession(
            bind=bind,
            expire_on_commit=False,
            commit_entered=entered,
            commit_release=release,
            inspect=inspect_terminal,
        )
        terminal_factory = ExistingSessionFactory(terminal_session)
        weights_session = harness.factory()
        observer = harness.factory()
        terminal_task: asyncio.Task[bool] | None = None
        weights_task: asyncio.Task[Any] | None = None
        try:
            actor = await load_actor(weights_session, harness.users["hr"])
            weights_pid = await weights_session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(weights_pid, int)
            terminal_task = asyncio.create_task(
                _terminal_update(
                    terminal_factory,
                    match_id=task_payload[0],
                    expected_generation=task_payload[1],
                    expected_resume_revision=task_payload[2],
                    expected_job_revision=task_payload[3],
                    algorithm_version=task_payload[4],
                    values=values,
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=5)
            assert terminal_session.pid is not None
            snapshot = terminal_session.snapshot
            assert snapshot[0] == status
            if status == "COMPLETED":
                assert snapshot[1:] == (
                    Decimal("80.00"),
                    [{"skill_id": 1}],
                    None,
                    "BAAI/bge-m3",
                    True,
                )
            else:
                assert snapshot[1:] == (
                    None,
                    [],
                    "controlled terminal failure",
                    None,
                    False,
                )
            weights_task = asyncio.create_task(
                update_job_weights(
                    weights_session,
                    current_user=actor,
                    job_id=job_id,
                    payload=weights(),
                    dispatcher=RecordingMatchDispatcher(),
                )
            )
            assert await wait_for_blocker(
                observer,
                waiter_pid=weights_pid,
                blocker_pid=terminal_session.pid,
            ) == (terminal_session.pid,)
            release.set()
            terminal_result, updated = await asyncio.wait_for(
                asyncio.gather(terminal_task, weights_task), timeout=10
            )
            assert terminal_result is True and updated.revision == 8
        finally:
            release.set()
            tasks = [task for task in (terminal_task, weights_task) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await terminal_session.close()
            await weights_session.close()
            await observer.close()
    else:
        entered = asyncio.Event()
        release = asyncio.Event()
        weights_session = CommitGateSession(
            bind=bind,
            expire_on_commit=False,
            commit_entered=entered,
            commit_release=release,
        )
        terminal_session = harness.factory()
        terminal_factory = ExistingSessionFactory(terminal_session)
        observer = harness.factory()
        weights_task: asyncio.Task[Any] | None = None
        terminal_task: asyncio.Task[bool] | None = None
        try:
            actor = await load_actor(weights_session, harness.users["hr"])
            weights_pid = await weights_session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(weights_pid, int)
            weights_task = asyncio.create_task(
                update_job_weights(
                    weights_session,
                    current_user=actor,
                    job_id=job_id,
                    payload=weights(),
                    dispatcher=RecordingMatchDispatcher(),
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=5)
            terminal_task = asyncio.create_task(
                _terminal_update(
                    terminal_factory,
                    match_id=task_payload[0],
                    expected_generation=task_payload[1],
                    expected_resume_revision=task_payload[2],
                    expected_job_revision=task_payload[3],
                    algorithm_version=task_payload[4],
                    values=values,
                )
            )
            await asyncio.wait_for(terminal_factory.entered.wait(), timeout=5)
            assert terminal_factory.pid is not None
            assert await wait_for_blocker(
                observer,
                waiter_pid=terminal_factory.pid,
                blocker_pid=weights_pid,
            ) == (weights_pid,)
            release.set()
            await asyncio.wait_for(weights_task, timeout=5)
            assert not await asyncio.wait_for(terminal_task, timeout=5)
        finally:
            release.set()
            tasks = [task for task in (weights_task, terminal_task) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await weights_session.close()
            await terminal_session.close()
            await observer.close()

    async with harness.factory() as verify:
        match = await verify.get(MatchResult, match_id)
        assert match is not None
        assert (match.generation, match.job_revision, match.status) == (
            original_generation + 1,
            8,
            "PENDING",
        )
        assert match.overall_score is None and match.skill_score is None
        assert match.semantic_score is None and match.experience_score is None
        assert match.matched_skills == [] and match.missing_skills == []
        assert match.gap_analysis_summary is None and match.error_message is None
        assert match.embedding_model is None and match.embedding_preprocessing_version is None
        assert match.calculated_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_first", [True, False], ids=["uc09-first", "weights-first"])
async def test_uc09_manual_resume_edit_and_weights_serialize_without_lock_inversion(
    job_update_postgres: JobUpdateHarness,
    resume_first: bool,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    resume_id = harness.resumes[0]
    match_id = harness.matches[0]
    async with harness.factory() as before:
        resume = await before.get(Resume, resume_id)
        match = await before.get(MatchResult, match_id)
        assert resume is not None and match is not None
        source = (
            resume.file_name,
            resume.storage_key,
            resume.file_size,
            resume.mime_type,
            resume.create_request_fingerprint,
            resume.raw_text,
        )
        old_resume_revision = resume.revision
        old_generation = match.generation

    parsed_payload = ResumeParsedDataUpdate.model_validate(
        {
            "candidate_profile": {"full_name": "UC18 UC09 candidate"},
            "skills": [{"skill_id": harness.skill_id, "years_of_experience": 3}],
            "experiences": [],
            "educations": [],
        }
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    bind = harness.factory.kw["bind"]
    winner = CommitGateSession(
        bind=bind,
        expire_on_commit=False,
        commit_entered=entered,
        commit_release=release,
    )
    waiter = harness.factory()
    observer = harness.factory()
    winner_task: asyncio.Task[Any] | None = None
    waiter_task: asyncio.Task[Any] | None = None

    async def run_resume(session: AsyncSession, actor: Any) -> Any:
        return await update_resume_parsed_data(
            session,
            current_user=actor,
            resume_id=resume_id,
            payload=parsed_payload,
            embedding_provider=ControlledEmbeddingProvider(),
        )

    async def run_weights(session: AsyncSession, actor: Any) -> Any:
        return await update_job_weights(
            session,
            current_user=actor,
            job_id=job_id,
            payload=weights(),
            dispatcher=RecordingMatchDispatcher(),
        )

    try:
        winner_actor = await load_actor(winner, harness.users["hr"])
        waiter_actor = await load_actor(waiter, harness.users["hr"])
        winner_pid = await winner.scalar(text("SELECT pg_backend_pid()"))
        waiter_pid = await waiter.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(winner_pid, int) and isinstance(waiter_pid, int)
        winner_task = asyncio.create_task(
            run_resume(winner, winner_actor) if resume_first else run_weights(winner, winner_actor)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        waiter_task = asyncio.create_task(
            run_weights(waiter, waiter_actor) if resume_first else run_resume(waiter, waiter_actor)
        )
        assert await wait_for_blocker(observer, waiter_pid=waiter_pid, blocker_pid=winner_pid) == (
            winner_pid,
        )
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(winner_task, waiter_task, return_exceptions=True), timeout=10
        )
        assert not [result for result in results if isinstance(result, BaseException)]
    finally:
        release.set()
        tasks = [task for task in (winner_task, waiter_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await winner.close()
        await waiter.close()
        await observer.close()

    async with harness.factory() as verify:
        job = await verify.get(JobDescription, job_id)
        resume = await verify.get(Resume, resume_id)
        match = await verify.get(MatchResult, match_id)
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == resume_id)
        )
        assert job is not None and resume is not None and match is not None
        assert (job.revision, resume.revision, match.generation) == (
            8,
            old_resume_revision + 1,
            old_generation + 2,
        )
        assert (match.resume_revision, match.job_revision, match.status) == (
            resume.revision,
            job.revision,
            "PENDING",
        )
        assert (
            resume.file_name,
            resume.storage_key,
            resume.file_size,
            resume.mime_type,
            resume.create_request_fingerprint,
            resume.raw_text,
        ) == source
        assert resume.is_manually_edited is True
        assert profile is not None and profile.full_name == "UC18 UC09 candidate"


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_first", [True, False], ids=["delete-first", "weights-first"])
async def test_job_soft_delete_and_weights_serialize_and_delayed_task_discards(
    job_update_postgres: JobUpdateHarness,
    delete_first: bool,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    bind = harness.factory.kw["bind"]
    entered = asyncio.Event()
    release = asyncio.Event()
    first = CommitGateSession(
        bind=bind,
        expire_on_commit=False,
        commit_entered=entered,
        commit_release=release,
    )
    second = harness.factory()
    observer = harness.factory()
    dispatcher = RecordingMatchDispatcher()
    first_task: asyncio.Task[Any] | None = None
    second_task: asyncio.Task[Any] | None = None

    async def run_delete(session: AsyncSession, actor: Any) -> Any:
        return await soft_delete_job(session, current_user=actor, job_id=job_id)

    async def run_weights(session: AsyncSession, actor: Any) -> Any:
        request = JobWeightsRequest.model_validate(
            {
                "w_skill": 0.2,
                "w_semantic": 0.7,
                "w_experience": 0.1,
                "recalculate": True,
            }
        )
        return await update_job_weights(
            session,
            current_user=actor,
            job_id=job_id,
            payload=request,
            dispatcher=dispatcher,
        )

    try:
        first_actor = await load_actor(first, harness.users["hr"])
        second_actor = await load_actor(second, harness.users["hr"])
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(first_pid, int) and isinstance(second_pid, int)
        first_task = asyncio.create_task(
            run_delete(first, first_actor) if delete_first else run_weights(first, first_actor)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        second_task = asyncio.create_task(
            run_weights(second, second_actor) if delete_first else run_delete(second, second_actor)
        )
        assert await wait_for_blocker(observer, waiter_pid=second_pid, blocker_pid=first_pid) == (
            first_pid,
        )
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(first_task, second_task, return_exceptions=True), timeout=10
        )
    finally:
        release.set()
        tasks = [task for task in (first_task, second_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await first.close()
        await second.close()
        await observer.close()

    failures = [result for result in results if isinstance(result, BaseException)]
    if delete_first:
        assert len(failures) == 1 and getattr(failures[0], "status_code", None) == 404
        assert dispatcher.calls == []
    else:
        assert failures == []
        assert len(dispatcher.calls) == 5
        outcome = await process_match_task(
            *dispatcher.calls[0],
            session_factory=harness.factory,
        )
        assert outcome is MatchTaskOutcome.DISCARDED

    async with harness.factory() as verify:
        job = await verify.get(JobDescription, job_id)
        rows = (await verify.scalars(select(MatchResult).where(MatchResult.job_id == job_id))).all()
        assert job is not None and job.is_deleted is True
        assert job.revision == (7 if delete_first else 8)
        assert all(row.job_revision == job.revision for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delete_first", [True, False], ids=["resume-delete-first", "weights-first"]
)
async def test_resume_soft_delete_and_weights_preserve_delete_and_worker_discards(
    job_update_postgres: JobUpdateHarness,
    delete_first: bool,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    resume_id = harness.resumes[0]
    match_id = harness.matches[0]
    async with harness.factory() as before:
        resume = await before.get(Resume, resume_id)
        match = await before.get(MatchResult, match_id)
        assert resume is not None and match is not None
        source = (
            resume.owner_user_id,
            resume.file_name,
            resume.storage_key,
            resume.file_size,
            resume.mime_type,
            resume.create_request_fingerprint,
            resume.raw_text,
            resume.revision,
            resume.resume_embedding,
        )
        old_generation = match.generation

    bind = harness.factory.kw["bind"]
    entered = asyncio.Event()
    release = asyncio.Event()
    first = CommitGateSession(
        bind=bind,
        expire_on_commit=False,
        commit_entered=entered,
        commit_release=release,
    )
    second = harness.factory()
    observer = harness.factory()
    dispatcher = RecordingMatchDispatcher()
    first_task: asyncio.Task[Any] | None = None
    second_task: asyncio.Task[Any] | None = None

    async def run_delete(session: AsyncSession, actor: Any) -> Any:
        return await soft_delete_resume(session, current_user=actor, resume_id=resume_id)

    async def run_weights(session: AsyncSession, actor: Any) -> Any:
        request = JobWeightsRequest.model_validate(
            {
                "w_skill": 0.2,
                "w_semantic": 0.7,
                "w_experience": 0.1,
                "recalculate": True,
            }
        )
        return await update_job_weights(
            session,
            current_user=actor,
            job_id=job_id,
            payload=request,
            dispatcher=dispatcher,
        )

    try:
        first_actor = await load_actor(first, harness.users["hr"])
        second_actor = await load_actor(second, harness.users["hr"])
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(first_pid, int) and isinstance(second_pid, int)
        first_task = asyncio.create_task(
            run_delete(first, first_actor) if delete_first else run_weights(first, first_actor)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        second_task = asyncio.create_task(
            run_weights(second, second_actor) if delete_first else run_delete(second, second_actor)
        )
        assert await wait_for_blocker(observer, waiter_pid=second_pid, blocker_pid=first_pid) == (
            first_pid,
        )
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(first_task, second_task, return_exceptions=True), timeout=10
        )
        assert not [result for result in results if isinstance(result, BaseException)]
    finally:
        release.set()
        tasks = [task for task in (first_task, second_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await first.close()
        await second.close()
        await observer.close()

    publication = next(call for call in dispatcher.calls if call[0] == match_id)
    assert (
        await process_match_task(
            *publication,
            session_factory=harness.factory,
        )
        is MatchTaskOutcome.DISCARDED
    )
    async with harness.factory() as verify:
        job = await verify.get(JobDescription, job_id)
        resume = await verify.get(Resume, resume_id)
        match = await verify.get(MatchResult, match_id)
        assert job is not None and resume is not None and match is not None
        assert resume.is_deleted is True and resume.deleted_at is not None
        assert (
            resume.owner_user_id,
            resume.file_name,
            resume.storage_key,
            resume.file_size,
            resume.mime_type,
            resume.create_request_fingerprint,
            resume.raw_text,
            resume.revision,
            resume.resume_embedding,
        ) == source
        assert (job.revision, match.generation, match.job_revision, match.status) == (
            8,
            old_generation + 1,
            8,
            "PENDING",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("competitor", ["trigger", "criteria", "raw", "weights"])
@pytest.mark.parametrize("weights_first", [True, False], ids=["weights-first", "competitor-first"])
async def test_cross_job_weights_uses_shared_resume_order_without_deadlock(
    job_update_postgres: JobUpdateHarness,
    competitor: str,
    weights_first: bool,
) -> None:
    harness = job_update_postgres
    job_a = harness.jobs["parsed"]
    job_b = harness.jobs["other"]
    shared_resumes = tuple(sorted(harness.resumes[:2], key=lambda value: value.int))
    reverse_ids = tuple(sorted((uuid.uuid4(), uuid.uuid4()), key=lambda value: value.int))

    async with harness.factory() as setup:
        persisted_b = await setup.get(JobDescription, job_b)
        assert persisted_b is not None
        setup.add(
            JobSkill(
                job_id=job_b,
                skill_id=harness.skill_id,
                importance="MANDATORY",
                min_years_required=Decimal("1.0"),
            )
        )
        resume_rows = {
            row.id: row
            for row in (
                await setup.scalars(select(Resume).where(Resume.id.in_(shared_resumes)))
            ).all()
        }
        source_snapshots = {
            row.id: (
                row.owner_user_id,
                row.file_name,
                row.storage_key,
                row.create_request_fingerprint,
                row.revision,
                row.parsing_status,
                row.raw_text,
                row.resume_embedding,
                row.embedding_model,
                row.embedding_preprocessing_version,
                row.is_deleted,
                row.deleted_at,
            )
            for row in resume_rows.values()
        }
        b_matches = (
            make_match(persisted_b, resume_rows[shared_resumes[1]], 50, "COMPLETED"),
            make_match(persisted_b, resume_rows[shared_resumes[0]], 51, "FAILED"),
        )
        # Match ids deliberately sort opposite to Resume ids.
        b_matches[0].id = reverse_ids[0]
        b_matches[1].id = reverse_ids[1]
        setup.add_all(b_matches)
        await setup.commit()
        initial = {
            row.id: (row.generation, row.job_id)
            for row in (
                await setup.scalars(
                    select(MatchResult).where(MatchResult.job_id.in_((job_a, job_b)))
                )
            ).all()
        }

    criteria = JobCriteriaRequest.model_validate(
        {
            "skills": [
                {
                    "skill_id": harness.skill_id,
                    "importance": "MANDATORY",
                    "min_years_required": 2,
                }
            ]
        }
    )

    async def run_weights(session: AsyncSession, actor: Any) -> Any:
        return await update_job_weights(
            session,
            current_user=actor,
            job_id=job_a,
            payload=weights(),
            dispatcher=RecordingMatchDispatcher(),
        )

    async def run_competitor(session: AsyncSession, actor: Any) -> Any:
        if competitor == "trigger":
            return await calculate_matches(
                session,
                current_user=actor,
                payload=MatchCalculateRequest(
                    job_id=job_b,
                    resume_ids=list(reversed(shared_resumes)),
                ),
                dispatcher=RecordingMatchDispatcher(),
            )
        if competitor == "criteria":
            return await update_job_criteria(
                session,
                current_user=actor,
                job_id=job_b,
                payload=criteria,
            )
        if competitor == "raw":
            return await update_job(
                session,
                current_user=actor,
                job_id=job_b,
                payload=JobUpdateRequest(raw_content="cross-job weights race"),
                dispatcher=RecordingDispatcher(),
            )
        return await update_job_weights(
            session,
            current_user=actor,
            job_id=job_b,
            payload=weights(skill=0.6),
            dispatcher=RecordingMatchDispatcher(),
        )

    blocker = harness.factory()
    first = harness.factory()
    second = harness.factory()
    observer = harness.factory()
    first_task: asyncio.Task[Any] | None = None
    second_task: asyncio.Task[Any] | None = None
    blocker_released = False
    try:
        first_actor = await load_actor(first, harness.users["admin"])
        second_actor = await load_actor(second, harness.users["admin"])
        blocker_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        assert all(isinstance(pid, int) for pid in (blocker_pid, first_pid, second_pid))
        assert len({blocker_pid, first_pid, second_pid}) == 3

        await blocker.scalar(select(Resume).where(Resume.id == shared_resumes[0]).with_for_update())
        first_task = asyncio.create_task(
            run_weights(first, first_actor) if weights_first else run_competitor(first, first_actor)
        )
        assert await wait_for_blocker(observer, waiter_pid=first_pid, blocker_pid=blocker_pid)
        second_task = asyncio.create_task(
            run_competitor(second, second_actor)
            if weights_first
            else run_weights(second, second_actor)
        )
        graph = await wait_graph_reaches(observer, waiter_pid=second_pid, blocker_pid=blocker_pid)
        assert graph[second_pid]
        await blocker.commit()
        blocker_released = True
        results = await asyncio.wait_for(
            asyncio.gather(first_task, second_task, return_exceptions=True), timeout=10
        )
        assert not [result for result in results if isinstance(result, BaseException)]
    finally:
        if not blocker_released:
            await blocker.rollback()
        tasks = [task for task in (first_task, second_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await blocker.close()
        await first.close()
        await second.close()
        await observer.close()

    async with harness.factory() as verify:
        jobs = {
            row.id: row
            for row in (
                await verify.scalars(
                    select(JobDescription).where(JobDescription.id.in_((job_a, job_b)))
                )
            ).all()
        }
        rows = (
            await verify.scalars(select(MatchResult).where(MatchResult.job_id.in_((job_a, job_b))))
        ).all()
        resumes = {
            row.id: row
            for row in (
                await verify.scalars(
                    select(Resume).where(Resume.id.in_({row.resume_id for row in rows}))
                )
            ).all()
        }
        assert {row.id for row in rows} == set(initial)
        assert jobs[job_a].revision == 8
        assert jobs[job_b].revision == (7 if competitor == "trigger" else 8)
        for row in rows:
            assert row.generation == initial[row.id][0] + 1
            assert row.job_revision == jobs[row.job_id].revision
            assert row.resume_revision == resumes[row.resume_id].revision
            assert row.status == "PENDING"
            assert row.overall_score is None and row.skill_score is None
            assert row.semantic_score is None and row.experience_score is None
            assert row.matched_skills == [] and row.missing_skills == []
            assert row.gap_analysis_summary is None and row.error_message is None
            assert row.embedding_model is None and row.embedding_preprocessing_version is None
            assert row.calculated_at is None
        for resume_id in shared_resumes:
            row = resumes[resume_id]
            assert (
                row.owner_user_id,
                row.file_name,
                row.storage_key,
                row.create_request_fingerprint,
                row.revision,
                row.parsing_status,
                row.raw_text,
                row.resume_embedding,
                row.embedding_model,
                row.embedding_preprocessing_version,
                row.is_deleted,
                row.deleted_at,
            ) == source_snapshots[resume_id]


@pytest.mark.asyncio
async def test_production_weights_resume_order_is_sensitive_to_exact_40p01_cycle(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    job_a = harness.jobs["parsed"]
    job_b = harness.jobs["other"]
    shared_resumes = tuple(sorted(harness.resumes[:2], key=lambda value: value.int))
    async with harness.factory() as setup:
        persisted_b = await setup.get(JobDescription, job_b)
        assert persisted_b is not None
        setup.add(
            JobSkill(
                job_id=job_b,
                skill_id=harness.skill_id,
                importance="MANDATORY",
                min_years_required=Decimal("1.0"),
            )
        )
        resumes = {
            row.id: row
            for row in (
                await setup.scalars(select(Resume).where(Resume.id.in_(shared_resumes)))
            ).all()
        }
        setup.add_all(
            (
                make_match(persisted_b, resumes[shared_resumes[1]], 60, "PENDING"),
                make_match(persisted_b, resumes[shared_resumes[0]], 61, "PENDING"),
            )
        )
        await setup.commit()

    blocker = harness.factory()
    criteria_session = harness.factory()
    weights_session = harness.factory()
    observer = harness.factory()
    criteria_task: asyncio.Task[Any] | None = None
    weights_task: asyncio.Task[Any] | None = None
    blocker_released = False
    listener_installed = False
    weights_sync_connection: Any = None
    reversals = 0

    def reverse_only_weights_resume_locks(
        _connection: Any,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> tuple[str, Any]:
        nonlocal reversals
        if (
            "FROM resumes" in statement
            and "FOR UPDATE" in statement
            and "ORDER BY resumes.id ASC" in statement
        ):
            reversals += 1
            statement = statement.replace("ORDER BY resumes.id ASC", "ORDER BY resumes.id DESC")
        return statement, parameters

    criteria = JobCriteriaRequest.model_validate(
        {
            "skills": [
                {
                    "skill_id": harness.skill_id,
                    "importance": "MANDATORY",
                    "min_years_required": 2,
                }
            ]
        }
    )
    try:
        criteria_actor = await load_actor(criteria_session, harness.users["admin"])
        weights_actor = await load_actor(weights_session, harness.users["admin"])
        blocker_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        criteria_pid = await criteria_session.scalar(text("SELECT pg_backend_pid()"))
        weights_pid = await weights_session.scalar(text("SELECT pg_backend_pid()"))
        assert len({blocker_pid, criteria_pid, weights_pid}) == 3
        connection = await weights_session.connection()
        weights_sync_connection = connection.sync_connection
        event.listen(
            weights_sync_connection,
            "before_cursor_execute",
            reverse_only_weights_resume_locks,
            retval=True,
        )
        listener_installed = True

        await blocker.scalar(select(Resume).where(Resume.id == shared_resumes[0]).with_for_update())
        criteria_task = asyncio.create_task(
            update_job_criteria(
                criteria_session,
                current_user=criteria_actor,
                job_id=job_b,
                payload=criteria,
            )
        )
        assert await wait_for_blocker(observer, waiter_pid=criteria_pid, blocker_pid=blocker_pid)
        weights_task = asyncio.create_task(
            update_job_weights(
                weights_session,
                current_user=weights_actor,
                job_id=job_a,
                payload=weights(),
                dispatcher=RecordingMatchDispatcher(),
            )
        )
        graph = await wait_graph_reaches(observer, waiter_pid=weights_pid, blocker_pid=blocker_pid)
        assert graph[weights_pid]
        await blocker.commit()
        blocker_released = True
        results = await asyncio.wait_for(
            asyncio.gather(criteria_task, weights_task, return_exceptions=True), timeout=10
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        assert reversals == 1
        assert len(failures) == 1
        assert isinstance(failures[0], DBAPIError)
        assert getattr(failures[0].orig, "sqlstate", None) == "40P01"
        assert len([result for result in results if not isinstance(result, BaseException)]) == 1
    finally:
        if not blocker_released:
            await blocker.rollback()
        tasks = [task for task in (criteria_task, weights_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if listener_installed:
            event.remove(
                weights_sync_connection,
                "before_cursor_execute",
                reverse_only_weights_resume_locks,
            )
        await blocker.close()
        await criteria_session.close()
        await weights_session.close()
        await observer.close()
