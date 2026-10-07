from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.engine_factory import create_engine
from app.core.exceptions import APIError
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume
from app.models.skill import JobSkill, Skill
from app.models.user import User
from app.schemas.job_schema import JobCriteriaRequest
from app.schemas.match_schema import MatchCalculateRequest
from app.services.job_service import update_job_criteria
from app.services.match_dispatcher import NoOpMatchDispatcher
from app.services.match_service import calculate_matches
from app.services.resume_service import soft_delete_resume
from app.workers import match_worker


@dataclass
class RecordingDispatcher:
    calls: list[tuple[uuid.UUID, int, int, int, str]] = field(default_factory=list)
    fail_at: int | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    expected_persisted: int | None = None

    async def dispatch(
        self,
        match_id: uuid.UUID,
        expected_generation: int,
        expected_resume_revision: int,
        expected_job_revision: int,
        algorithm_version: str,
    ) -> None:
        if self.session_factory is not None and self.expected_persisted is not None:
            async with self.session_factory() as observer:
                count = await observer.scalar(select(func.count()).select_from(MatchResult))
                assert count is not None and count >= self.expected_persisted
        self.calls.append(
            (
                match_id,
                expected_generation,
                expected_resume_revision,
                expected_job_revision,
                algorithm_version,
            )
        )
        if self.fail_at == len(self.calls):
            raise RuntimeError("broker unavailable with secret details")


@dataclass(frozen=True)
class MatchTriggerHarness:
    session_factory: async_sessionmaker[AsyncSession]
    candidate: User
    hr: User
    other_hr: User
    admin: User
    job: JobDescription
    other_job: JobDescription
    hr_resumes: tuple[Resume, ...]
    candidate_resume: Resume
    other_resume: Resume
    skill_id: int


def _user(role: str, unique: str, name: str) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=f"match-trigger.{unique}.{name}@example.com",
        password_hash="not-used",
        full_name=f"Match Trigger {name}",
        phone_number=None,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _resume(unique: str, name: str, owner: User, *, ready: bool = True) -> Resume:
    now = datetime.now(UTC)
    return Resume(
        id=uuid.uuid4(),
        owner_user_id=owner.id,
        file_name=f"{name}.pdf",
        storage_key=f"match-trigger/{unique}/{name}",
        file_size=128,
        mime_type="application/pdf",
        create_request_fingerprint=uuid.uuid4().hex * 2,
        revision=2,
        parsing_status="PARSED" if ready else "PENDING",
        raw_text=f"Resume {name}" if ready else None,
        resume_embedding=[0.25] * 1024 if ready else None,
        embedding_model="BAAI/bge-m3" if ready else None,
        embedding_preprocessing_version="semantic-v1" if ready else None,
        error_message=None,
        is_manually_edited=False,
        is_deleted=False,
        created_at=now,
        updated_at=now,
        parsed_at=now if ready else None,
        deleted_at=None,
    )


def _job(unique: str, name: str, recruiter: User, *, active: bool = True) -> JobDescription:
    now = datetime.now(UTC)
    return JobDescription(
        id=uuid.uuid4(),
        recruiter_id=recruiter.id,
        title=f"Match trigger {name}",
        job_level="SENIOR",
        location="Hanoi",
        raw_content=f"Job {name}",
        create_request_fingerprint=uuid.uuid4().hex * 2,
        revision=3,
        min_experience_years=Decimal("2.0"),
        education_requirement=None,
        job_embedding=[0.25] * 1024,
        embedding_model="BAAI/bge-m3",
        embedding_preprocessing_version="semantic-v1",
        parsing_status="PARSED",
        parsing_error_message=None,
        is_criteria_verified=True,
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


@pytest_asyncio.fixture
async def match_trigger_postgres() -> AsyncIterator[MatchTriggerHarness]:
    configured = Settings()
    if configured.database_url is None:
        pytest.skip("DATABASE_URL is not configured")
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=configured.database_url,
        database_connect_timeout_seconds=configured.database_connect_timeout_seconds,
    )
    engine = create_engine(settings)
    assert engine is not None
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 - never expose database credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    unique = uuid.uuid4().hex
    candidate = _user("CANDIDATE", unique, "candidate")
    hr = _user("HR", unique, "hr")
    other_hr = _user("HR", unique, "other-hr")
    admin = _user("ADMIN", unique, "admin")
    tracked_user_ids = (candidate.id, hr.id, other_hr.id, admin.id)
    async with session_factory() as session:
        skill_id = await session.scalar(select(Skill.id).order_by(Skill.id.asc()).limit(1))
        if skill_id is None:
            await engine.dispose()
            pytest.skip("The configured database has no canonical skills")
        session.add_all((candidate, hr, other_hr, admin))
        await session.flush()
        job = _job(unique, "primary", hr)
        other_job = _job(unique, "other", hr)
        hr_resumes = tuple(_resume(unique, f"hr-{index}", hr) for index in range(5))
        candidate_resume = _resume(unique, "candidate", candidate)
        other_resume = _resume(unique, "other", other_hr)
        session.add_all((job, other_job, *hr_resumes, candidate_resume, other_resume))
        await session.flush()
        session.add_all(
            (
                JobSkill(
                    job_id=job.id,
                    skill_id=skill_id,
                    importance="MANDATORY",
                    min_years_required=Decimal("1.0"),
                ),
                JobSkill(
                    job_id=other_job.id,
                    skill_id=skill_id,
                    importance="MANDATORY",
                    min_years_required=Decimal("1.0"),
                ),
            )
        )
        await session.commit()

    try:
        yield MatchTriggerHarness(
            session_factory,
            candidate,
            hr,
            other_hr,
            admin,
            job,
            other_job,
            hr_resumes,
            candidate_resume,
            other_resume,
            skill_id,
        )
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(delete(User).where(User.id.in_(tracked_user_ids)))
            await cleanup.commit()
        await engine.dispose()


def _request(job: JobDescription, resumes: tuple[Resume, ...]) -> MatchCalculateRequest:
    return MatchCalculateRequest(job_id=job.id, resume_ids=[resume.id for resume in resumes])


def _stale_match(job: JobDescription, resume: Resume, status: str, generation: int) -> MatchResult:
    now = datetime.now(UTC)
    completed = status == "COMPLETED"
    return MatchResult(
        id=uuid.uuid4(),
        job_id=job.id,
        resume_id=resume.id,
        generation=generation,
        resume_revision=1,
        job_revision=1,
        overall_score=Decimal("88.00") if completed else None,
        skill_score=Decimal("89.00") if completed else None,
        semantic_score=Decimal("87.00") if completed else None,
        experience_score=Decimal("88.00") if completed else None,
        matched_skills=[{"skill_id": 1}] if completed else [],
        missing_skills=[{"skill_id": 2}] if completed else [],
        gap_analysis_summary="stale" if completed else None,
        algorithm_version="hybrid-v1",
        embedding_model="old-model" if completed else None,
        embedding_preprocessing_version="old-version" if completed else None,
        status=status,
        error_message="stale failure" if status == "FAILED" else None,
        created_at=now,
        updated_at=now,
        calculated_at=now if completed else None,
    )


async def _wait_for_database_blocker(
    observer: AsyncSession,
    *,
    waiter_pid: int,
    blocker_pid: int,
) -> None:
    async with asyncio.timeout(5):
        while True:
            blockers = await observer.scalar(
                text("SELECT pg_blocking_pids(:pid)"),
                {"pid": waiter_pid},
            )
            if blocker_pid in blockers:
                return


@pytest.mark.asyncio
async def test_real_postgres_trigger_creates_batch_and_dispatches_after_commit(
    match_trigger_postgres: MatchTriggerHarness,
) -> None:
    harness = match_trigger_postgres
    dispatcher = RecordingDispatcher(
        session_factory=harness.session_factory,
        expected_persisted=3,
    )
    requested = (harness.hr_resumes[2], harness.hr_resumes[0], harness.hr_resumes[1])
    resource_before = {
        resume.id: (
            resume.revision,
            resume.parsing_status,
            tuple(resume.resume_embedding or ()),
            resume.embedding_model,
            resume.embedding_preprocessing_version,
        )
        for resume in requested
    }
    job_before = (
        harness.job.revision,
        harness.job.parsing_status,
        tuple(harness.job.job_embedding or ()),
        harness.job.is_criteria_verified,
        harness.job.w_skill,
        harness.job.w_semantic,
        harness.job.w_experience,
    )
    async with harness.session_factory() as session:
        match_ids = await calculate_matches(
            session,
            current_user=harness.hr,
            payload=_request(harness.job, requested),
            dispatcher=dispatcher,
        )

    assert [call[0] for call in dispatcher.calls] == match_ids
    assert len(set(match_ids)) == 3
    async with harness.session_factory() as observer:
        rows = list(
            (await observer.scalars(select(MatchResult).where(MatchResult.id.in_(match_ids)))).all()
        )
    by_id = {row.id: row for row in rows}
    assert [by_id[match_id].resume_id for match_id in match_ids] == [
        resume.id for resume in requested
    ]
    for row, call in zip((by_id[item] for item in match_ids), dispatcher.calls, strict=True):
        assert (row.generation, row.resume_revision, row.job_revision) == (1, 2, 3)
        assert row.status == "PENDING"
        assert call == (row.id, 1, 2, 3, "hybrid-v1")
    async with harness.session_factory() as observer:
        persisted_job = await observer.get(JobDescription, harness.job.id)
        persisted_resumes = list(
            (await observer.scalars(select(Resume).where(Resume.id.in_(resource_before)))).all()
        )
    assert persisted_job is not None
    assert (
        persisted_job.revision,
        persisted_job.parsing_status,
        tuple(persisted_job.job_embedding or ()),
        persisted_job.is_criteria_verified,
        persisted_job.w_skill,
        persisted_job.w_semantic,
        persisted_job.w_experience,
    ) == job_before
    assert {
        resume.id: (
            resume.revision,
            resume.parsing_status,
            tuple(resume.resume_embedding or ()),
            resume.embedding_model,
            resume.embedding_preprocessing_version,
        )
        for resume in persisted_resumes
    } == resource_before


@pytest.mark.asyncio
async def test_real_postgres_retrigger_all_states_preserves_ids_and_clears_payload(
    match_trigger_postgres: MatchTriggerHarness,
) -> None:
    harness = match_trigger_postgres
    states = ("PENDING", "PROCESSING", "COMPLETED", "FAILED")
    stale_rows = tuple(
        _stale_match(harness.job, resume, state, index + 4)
        for index, (resume, state) in enumerate(zip(harness.hr_resumes, states, strict=False))
    )
    async with harness.session_factory() as setup:
        setup.add_all(stale_rows)
        await setup.commit()

    dispatcher = RecordingDispatcher()
    async with harness.session_factory() as session:
        ids = await calculate_matches(
            session,
            current_user=harness.hr,
            payload=_request(harness.job, harness.hr_resumes[:4]),
            dispatcher=dispatcher,
        )
    assert ids == [row.id for row in stale_rows]

    async with harness.session_factory() as observer:
        refreshed = list(
            (
                await observer.scalars(
                    select(MatchResult)
                    .where(MatchResult.id.in_(ids))
                    .order_by(MatchResult.resume_id.asc())
                )
            ).all()
        )
    original_by_id = {row.id: row for row in stale_rows}
    for row in refreshed:
        assert row.generation == original_by_id[row.id].generation + 1
        assert (row.resume_revision, row.job_revision, row.algorithm_version) == (
            2,
            3,
            "hybrid-v1",
        )
        assert row.status == "PENDING"
        assert (
            row.overall_score,
            row.skill_score,
            row.semantic_score,
            row.experience_score,
            row.gap_analysis_summary,
            row.error_message,
            row.embedding_model,
            row.embedding_preprocessing_version,
            row.calculated_at,
        ) == (None,) * 9
        assert row.matched_skills == [] and row.missing_skills == []


@pytest.mark.asyncio
async def test_real_postgres_full_batch_validation_is_non_mutating_and_never_dispatches(
    match_trigger_postgres: MatchTriggerHarness,
) -> None:
    harness = match_trigger_postgres
    valid, invalid = harness.hr_resumes[:2]
    existing = _stale_match(harness.job, valid, "COMPLETED", 9)
    async with harness.session_factory() as setup:
        setup.add(existing)
        locked_invalid = await setup.get(Resume, invalid.id)
        assert locked_invalid is not None
        locked_invalid.parsing_status = "PENDING"
        locked_invalid.raw_text = None
        locked_invalid.resume_embedding = None
        locked_invalid.embedding_model = None
        locked_invalid.embedding_preprocessing_version = None
        locked_invalid.parsed_at = None
        await setup.commit()

    before = (existing.generation, existing.status, existing.overall_score, existing.updated_at)
    dispatcher = RecordingDispatcher()
    async with harness.session_factory() as session:
        with pytest.raises(APIError) as caught:
            await calculate_matches(
                session,
                current_user=harness.hr,
                payload=_request(harness.job, (valid, invalid)),
                dispatcher=dispatcher,
            )
    assert caught.value.code == "RESUME_NOT_READY"
    assert dispatcher.calls == []
    async with harness.session_factory() as observer:
        persisted = await observer.get(MatchResult, existing.id)
        created = await observer.scalar(
            select(MatchResult).where(
                MatchResult.job_id == harness.job.id,
                MatchResult.resume_id == invalid.id,
            )
        )
    assert persisted is not None
    assert (
        persisted.generation,
        persisted.status,
        persisted.overall_score,
        persisted.updated_at,
    ) == before
    assert created is None


@pytest.mark.asyncio
async def test_real_postgres_prepare_failure_rolls_back_whole_batch(
    match_trigger_postgres: MatchTriggerHarness,
) -> None:
    harness = match_trigger_postgres
    dispatcher = RecordingDispatcher()
    async with harness.session_factory() as session:
        original_flush = session.flush

        async def fail_after_flush(*args: Any, **kwargs: Any) -> None:
            await original_flush(*args, **kwargs)
            raise RuntimeError("forced prepare failure")

        session.flush = fail_after_flush  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="forced prepare failure"):
            await calculate_matches(
                session,
                current_user=harness.hr,
                payload=_request(harness.job, harness.hr_resumes[:2]),
                dispatcher=dispatcher,
            )
    assert dispatcher.calls == []
    async with harness.session_factory() as observer:
        count = await observer.scalar(
            select(func.count())
            .select_from(MatchResult)
            .where(MatchResult.job_id == harness.job.id)
        )
    assert count == 0


@pytest.mark.asyncio
async def test_real_postgres_no_broker_mode_keeps_prepared_row_pending(
    match_trigger_postgres: MatchTriggerHarness,
) -> None:
    harness = match_trigger_postgres
    async with harness.session_factory() as session:
        match_ids = await calculate_matches(
            session,
            current_user=harness.hr,
            payload=_request(harness.job, (harness.hr_resumes[0],)),
            dispatcher=NoOpMatchDispatcher(),
        )
    async with harness.session_factory() as observer:
        persisted = await observer.get(MatchResult, match_ids[0])
    assert persisted is not None and persisted.status == "PENDING"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing_job", "JOB_NOT_FOUND"),
        ("deleted_job", "JOB_NOT_FOUND"),
        ("job_not_parsed", "JOB_NOT_READY"),
        ("criteria_unverified", "JOB_NOT_READY"),
        ("missing_job_skills", "JOB_NOT_READY"),
        ("deleted_resume", "RESUME_NOT_FOUND"),
        ("missing_resume", "RESUME_NOT_FOUND"),
        ("resume_not_parsed", "RESUME_NOT_READY"),
    ],
)
async def test_real_postgres_missing_deleted_and_readiness_failures_do_not_prepare(
    match_trigger_postgres: MatchTriggerHarness,
    case: str,
    expected_code: str,
) -> None:
    harness = match_trigger_postgres
    job_id = harness.job.id
    resume_id = harness.hr_resumes[0].id
    async with harness.session_factory() as setup:
        if case == "deleted_job":
            job = await setup.get(JobDescription, job_id)
            assert job is not None
            job.is_deleted = True
            job.deleted_at = datetime.now(UTC)
        elif case == "job_not_parsed":
            job = await setup.get(JobDescription, job_id)
            assert job is not None
            job.status = "DRAFT"
            job.parsing_status = "PENDING"
            job.job_embedding = None
            job.embedding_model = None
            job.embedding_preprocessing_version = None
            job.parsed_at = None
        elif case == "criteria_unverified":
            job = await setup.get(JobDescription, job_id)
            assert job is not None
            job.status = "DRAFT"
            job.is_criteria_verified = False
        elif case == "missing_job_skills":
            await setup.execute(delete(JobSkill).where(JobSkill.job_id == job_id))
        elif case == "deleted_resume":
            resume = await setup.get(Resume, resume_id)
            assert resume is not None
            resume.is_deleted = True
            resume.deleted_at = datetime.now(UTC)
        elif case == "resume_not_parsed":
            resume = await setup.get(Resume, resume_id)
            assert resume is not None
            resume.parsing_status = "PENDING"
            resume.raw_text = None
            resume.resume_embedding = None
            resume.embedding_model = None
            resume.embedding_preprocessing_version = None
            resume.parsed_at = None
        await setup.commit()

    if case == "missing_job":
        job_id = uuid.uuid4()
    if case == "missing_resume":
        resume_id = uuid.uuid4()
    dispatcher = RecordingDispatcher()
    payload = MatchCalculateRequest(job_id=job_id, resume_ids=[resume_id])
    async with harness.session_factory() as session:
        with pytest.raises(APIError) as caught:
            await calculate_matches(
                session,
                current_user=harness.hr,
                payload=payload,
                dispatcher=dispatcher,
            )
    assert caught.value.code == expected_code
    assert dispatcher.calls == []
    async with harness.session_factory() as observer:
        count = await observer.scalar(select(func.count()).select_from(MatchResult))
    assert count == 0


@pytest.mark.asyncio
async def test_real_postgres_dispatch_failure_keeps_rows_and_retry_advances_generation(
    match_trigger_postgres: MatchTriggerHarness,
) -> None:
    harness = match_trigger_postgres
    requested = harness.hr_resumes[:3]
    failing = RecordingDispatcher(fail_at=2)
    async with harness.session_factory() as session:
        with pytest.raises(APIError) as caught:
            await calculate_matches(
                session,
                current_user=harness.hr,
                payload=_request(harness.job, requested),
                dispatcher=failing,
            )
    assert caught.value.status_code == 503
    assert caught.value.code == "TASK_DISPATCH_FAILED"
    assert len(failing.calls) == 2

    async with harness.session_factory() as observer:
        rows = list(
            (
                await observer.scalars(
                    select(MatchResult).where(MatchResult.job_id == harness.job.id)
                )
            ).all()
        )
    assert len(rows) == 3
    assert {row.status for row in rows} == {"PENDING"}
    first_ids = {row.resume_id: row.id for row in rows}

    retry_dispatcher = RecordingDispatcher()
    async with harness.session_factory() as retry_session:
        retry_ids = await calculate_matches(
            retry_session,
            current_user=harness.hr,
            payload=_request(harness.job, requested),
            dispatcher=retry_dispatcher,
        )
    assert retry_ids == [first_ids[resume.id] for resume in requested]
    assert {call[1] for call in retry_dispatcher.calls} == {2}


@pytest.mark.asyncio
async def test_real_postgres_authorization_active_and_embedding_readiness(
    match_trigger_postgres: MatchTriggerHarness,
) -> None:
    harness = match_trigger_postgres
    dispatcher = RecordingDispatcher()

    async with harness.session_factory() as candidate_session:
        ids = await calculate_matches(
            candidate_session,
            current_user=harness.candidate,
            payload=_request(harness.job, (harness.candidate_resume,)),
            dispatcher=dispatcher,
        )
    assert len(ids) == 1

    async with harness.session_factory() as denied_session:
        with pytest.raises(APIError) as denied:
            await calculate_matches(
                denied_session,
                current_user=harness.hr,
                payload=_request(harness.job, (harness.other_resume,)),
                dispatcher=RecordingDispatcher(),
            )
    assert denied.value.status_code == 404 and denied.value.code == "RESUME_NOT_FOUND"

    async with harness.session_factory() as admin_success_session:
        admin_ids = await calculate_matches(
            admin_success_session,
            current_user=harness.admin,
            payload=_request(harness.job, (harness.other_resume,)),
            dispatcher=RecordingDispatcher(),
        )
    assert len(admin_ids) == 1

    async with harness.session_factory() as wrong_job_session:
        with pytest.raises(APIError) as wrong_job:
            await calculate_matches(
                wrong_job_session,
                current_user=harness.other_hr,
                payload=_request(harness.job, (harness.other_resume,)),
                dispatcher=RecordingDispatcher(),
            )
    assert wrong_job.value.status_code == 404 and wrong_job.value.code == "JOB_NOT_FOUND"

    async with harness.session_factory() as mutate:
        job = await mutate.get(JobDescription, harness.other_job.id)
        assert job is not None
        job.status = "DRAFT"
        await mutate.commit()
    async with harness.session_factory() as inactive_session:
        with pytest.raises(APIError) as inactive:
            await calculate_matches(
                inactive_session,
                current_user=harness.candidate,
                payload=_request(harness.other_job, (harness.candidate_resume,)),
                dispatcher=RecordingDispatcher(),
            )
    assert inactive.value.status_code == 404 and inactive.value.code == "JOB_NOT_FOUND"

    async with harness.session_factory() as mismatch_setup:
        resume = await mismatch_setup.get(Resume, harness.other_resume.id)
        assert resume is not None
        resume.embedding_preprocessing_version = "other-version"
        await mismatch_setup.commit()
    async with harness.session_factory() as admin_session:
        with pytest.raises(APIError) as mismatch:
            await calculate_matches(
                admin_session,
                current_user=harness.admin,
                payload=_request(harness.job, (harness.other_resume,)),
                dispatcher=RecordingDispatcher(),
            )
    assert mismatch.value.code == "EMBEDDING_VERSION_MISMATCH"


@pytest.mark.asyncio
async def test_real_postgres_concurrent_same_batch_reversed_order_is_serialized(
    match_trigger_postgres: MatchTriggerHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = match_trigger_postgres
    first_order = harness.hr_resumes[:3]
    second_order = tuple(reversed(first_order))
    first_has_job_lock = asyncio.Event()
    release_first = asyncio.Event()
    async with (
        harness.session_factory() as first_session,
        harness.session_factory() as second_session,
        harness.session_factory() as observer,
    ):
        first_pid = await first_session.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second_session.scalar(text("SELECT pg_backend_pid()"))
        assert first_pid is not None and second_pid is not None and first_pid != second_pid
        original_scalar = first_session.scalar

        async def gate_after_job_lock(*args: Any, **kwargs: Any) -> Any:
            result = await original_scalar(*args, **kwargs)
            if isinstance(result, JobDescription) and not first_has_job_lock.is_set():
                first_has_job_lock.set()
                await release_first.wait()
            return result

        monkeypatch.setattr(first_session, "scalar", gate_after_job_lock)
        first_task = asyncio.create_task(
            calculate_matches(
                first_session,
                current_user=harness.hr,
                payload=_request(harness.job, first_order),
                dispatcher=RecordingDispatcher(),
            )
        )
        await first_has_job_lock.wait()
        second_task = asyncio.create_task(
            calculate_matches(
                second_session,
                current_user=harness.hr,
                payload=_request(harness.job, second_order),
                dispatcher=RecordingDispatcher(),
            )
        )
        try:
            await _wait_for_database_blocker(
                observer,
                waiter_pid=second_pid,
                blocker_pid=first_pid,
            )
        finally:
            release_first.set()
        first_ids, second_ids = await asyncio.wait_for(
            asyncio.gather(first_task, second_task),
            timeout=10,
        )
    assert first_ids == list(reversed(second_ids))
    async with harness.session_factory() as observer:
        rows = list(
            (
                await observer.scalars(
                    select(MatchResult).where(MatchResult.job_id == harness.job.id)
                )
            ).all()
        )
    assert len(rows) == 3
    assert {row.generation for row in rows} == {2}


@pytest.mark.asyncio
async def test_real_postgres_concurrent_different_jobs_with_overlapping_resume_completes(
    match_trigger_postgres: MatchTriggerHarness,
) -> None:
    harness = match_trigger_postgres
    resume = harness.hr_resumes[0]

    async def trigger(job: JobDescription) -> list[uuid.UUID]:
        async with harness.session_factory() as session:
            return await calculate_matches(
                session,
                current_user=harness.hr,
                payload=_request(job, (resume,)),
                dispatcher=RecordingDispatcher(),
            )

    results = await asyncio.wait_for(
        asyncio.gather(trigger(harness.job), trigger(harness.other_job)),
        timeout=10,
    )
    assert len({item[0] for item in results}) == 2
    async with harness.session_factory() as observer:
        count = await observer.scalar(
            select(func.count()).select_from(MatchResult).where(MatchResult.resume_id == resume.id)
        )
    assert count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("first_lock", ["trigger", "criteria"])
async def test_real_postgres_trigger_and_criteria_update_serialize_on_job_lock(
    match_trigger_postgres: MatchTriggerHarness,
    monkeypatch: pytest.MonkeyPatch,
    first_lock: str,
) -> None:
    harness = match_trigger_postgres
    resume = harness.hr_resumes[0]
    criteria = JobCriteriaRequest.model_validate(
        {
            "min_experience_years": 4.0,
            "skills": [
                {
                    "skill_id": harness.skill_id,
                    "importance": "MANDATORY",
                    "min_years_required": 2.0,
                }
            ],
        }
    )

    first_has_job_lock = asyncio.Event()
    release_first = asyncio.Event()
    async with (
        harness.session_factory() as trigger_session,
        harness.session_factory() as criteria_session,
        harness.session_factory() as observer,
    ):
        actor = await criteria_session.get(User, harness.hr.id)
        assert actor is not None
        trigger_pid = await trigger_session.scalar(text("SELECT pg_backend_pid()"))
        criteria_pid = await criteria_session.scalar(text("SELECT pg_backend_pid()"))
        assert trigger_pid is not None and criteria_pid is not None
        gated_session = trigger_session if first_lock == "trigger" else criteria_session
        original_scalar = gated_session.scalar

        async def gate_after_job_lock(*args: Any, **kwargs: Any) -> Any:
            result = await original_scalar(*args, **kwargs)
            if isinstance(result, JobDescription) and not first_has_job_lock.is_set():
                first_has_job_lock.set()
                await release_first.wait()
            return result

        monkeypatch.setattr(gated_session, "scalar", gate_after_job_lock)

        async def trigger() -> object:
            try:
                return await calculate_matches(
                    trigger_session,
                    current_user=harness.hr,
                    payload=_request(harness.job, (resume,)),
                    dispatcher=RecordingDispatcher(),
                )
            except APIError as error:
                return error

        async def replace_criteria() -> None:
            await update_job_criteria(
                criteria_session,
                current_user=actor,
                job_id=harness.job.id,
                payload=criteria,
            )

        first = trigger if first_lock == "trigger" else replace_criteria
        second = replace_criteria if first_lock == "trigger" else trigger
        first_task = asyncio.create_task(first())
        await first_has_job_lock.wait()
        second_task = asyncio.create_task(second())
        try:
            await _wait_for_database_blocker(
                observer,
                waiter_pid=criteria_pid if first_lock == "trigger" else trigger_pid,
                blocker_pid=trigger_pid if first_lock == "trigger" else criteria_pid,
            )
        finally:
            release_first.set()
        first_result, second_result = await asyncio.wait_for(
            asyncio.gather(first_task, second_task),
            timeout=10,
        )
        trigger_result = first_result if first_lock == "trigger" else second_result
    assert not isinstance(trigger_result, APIError)
    async with harness.session_factory() as observer:
        job = await observer.get(JobDescription, harness.job.id)
        match = await observer.scalar(
            select(MatchResult).where(
                MatchResult.job_id == harness.job.id,
                MatchResult.resume_id == resume.id,
            )
        )
    assert job is not None and match is not None
    assert job.revision == 4
    assert match.job_revision == job.revision
    assert match.resume_revision == resume.revision
    assert match.status == "PENDING"
    assert match.generation in {1, 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("first_lock", ["trigger", "delete"])
async def test_real_postgres_trigger_and_soft_delete_serialize_without_stale_success(
    match_trigger_postgres: MatchTriggerHarness,
    monkeypatch: pytest.MonkeyPatch,
    first_lock: str,
) -> None:
    harness = match_trigger_postgres
    resume = harness.hr_resumes[0]

    first_has_resume_lock = asyncio.Event()
    release_first = asyncio.Event()
    async with (
        harness.session_factory() as trigger_session,
        harness.session_factory() as delete_session,
        harness.session_factory() as observer,
    ):
        actor = await delete_session.get(User, harness.hr.id)
        assert actor is not None
        trigger_pid = await trigger_session.scalar(text("SELECT pg_backend_pid()"))
        delete_pid = await delete_session.scalar(text("SELECT pg_backend_pid()"))
        assert trigger_pid is not None and delete_pid is not None

        if first_lock == "trigger":
            original_scalars = trigger_session.scalars

            async def gate_after_resume_lock(*args: Any, **kwargs: Any) -> Any:
                result = await original_scalars(*args, **kwargs)
                if not first_has_resume_lock.is_set():
                    first_has_resume_lock.set()
                    await release_first.wait()
                return result

            monkeypatch.setattr(trigger_session, "scalars", gate_after_resume_lock)
        else:
            original_scalar = delete_session.scalar

            async def gate_after_resume_lock(*args: Any, **kwargs: Any) -> Any:
                result = await original_scalar(*args, **kwargs)
                if isinstance(result, Resume) and not first_has_resume_lock.is_set():
                    first_has_resume_lock.set()
                    await release_first.wait()
                return result

            monkeypatch.setattr(delete_session, "scalar", gate_after_resume_lock)

        async def trigger() -> object:
            try:
                return await calculate_matches(
                    trigger_session,
                    current_user=harness.hr,
                    payload=_request(harness.job, (resume,)),
                    dispatcher=RecordingDispatcher(),
                )
            except APIError as error:
                return error

        async def remove_resume() -> None:
            await soft_delete_resume(delete_session, current_user=actor, resume_id=resume.id)

        first = trigger if first_lock == "trigger" else remove_resume
        second = remove_resume if first_lock == "trigger" else trigger
        first_task = asyncio.create_task(first())
        await first_has_resume_lock.wait()
        second_task = asyncio.create_task(second())
        try:
            await _wait_for_database_blocker(
                observer,
                waiter_pid=delete_pid if first_lock == "trigger" else trigger_pid,
                blocker_pid=trigger_pid if first_lock == "trigger" else delete_pid,
            )
        finally:
            release_first.set()
        first_result, second_result = await asyncio.wait_for(
            asyncio.gather(first_task, second_task),
            timeout=10,
        )
        trigger_result = first_result if first_lock == "trigger" else second_result
    async with harness.session_factory() as observer:
        persisted_resume = await observer.get(Resume, resume.id)
        match = await observer.scalar(
            select(MatchResult).where(
                MatchResult.job_id == harness.job.id,
                MatchResult.resume_id == resume.id,
            )
        )
    assert persisted_resume is not None and persisted_resume.is_deleted
    if isinstance(trigger_result, APIError):
        assert trigger_result.status_code == 404
        assert match is None
    else:
        assert match is not None and match.status == "PENDING"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["COMPLETED", "FAILED"])
@pytest.mark.parametrize("first_lock", ["trigger", "terminal"])
async def test_real_postgres_trigger_and_terminal_write_serialize_both_lock_orders(
    match_trigger_postgres: MatchTriggerHarness,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
    first_lock: str,
) -> None:
    harness = match_trigger_postgres
    resume = harness.hr_resumes[0]
    match = _stale_match(harness.job, resume, "PROCESSING", 5)
    match.resume_revision = resume.revision
    match.job_revision = harness.job.revision
    async with harness.session_factory() as setup:
        setup.add(match)
        await setup.commit()

    first_has_job_lock = asyncio.Event()
    release_first = asyncio.Event()
    async with (
        harness.session_factory() as trigger_session,
        harness.session_factory() as terminal_session,
        harness.session_factory() as observer,
    ):
        trigger_pid = await trigger_session.scalar(text("SELECT pg_backend_pid()"))
        terminal_pid = await terminal_session.scalar(text("SELECT pg_backend_pid()"))
        assert trigger_pid is not None and terminal_pid is not None
        gated_session = trigger_session if first_lock == "trigger" else terminal_session
        original_scalar = gated_session.scalar

        async def gate_after_job_lock(*args: Any, **kwargs: Any) -> Any:
            result = await original_scalar(*args, **kwargs)
            if isinstance(result, JobDescription) and not first_has_job_lock.is_set():
                first_has_job_lock.set()
                await release_first.wait()
            return result

        monkeypatch.setattr(gated_session, "scalar", gate_after_job_lock)

        async def trigger() -> list[uuid.UUID]:
            return await calculate_matches(
                trigger_session,
                current_user=harness.hr,
                payload=_request(harness.job, (resume,)),
                dispatcher=RecordingDispatcher(),
            )

        async def terminal() -> bool:
            now = datetime.now(UTC)
            values: dict[str, Any]
            if terminal_status == "COMPLETED":
                values = {
                    "status": "COMPLETED",
                    "overall_score": Decimal("80.00"),
                    "skill_score": Decimal("80.00"),
                    "semantic_score": Decimal("80.00"),
                    "experience_score": Decimal("80.00"),
                    "matched_skills": [],
                    "missing_skills": [],
                    "gap_analysis_summary": None,
                    "embedding_model": "BAAI/bge-m3",
                    "embedding_preprocessing_version": "semantic-v1",
                    "error_message": None,
                    "calculated_at": now,
                    "updated_at": now,
                }
            else:
                values = {
                    "status": "FAILED",
                    "error_message": "controlled failure",
                    "updated_at": now,
                }
            return await match_worker._terminal_update(
                lambda: terminal_session,
                match_id=match.id,
                expected_generation=5,
                expected_resume_revision=resume.revision,
                expected_job_revision=harness.job.revision,
                algorithm_version="hybrid-v1",
                values=values,
            )

        first = trigger if first_lock == "trigger" else terminal
        second = terminal if first_lock == "trigger" else trigger
        first_task = asyncio.create_task(first())
        await first_has_job_lock.wait()
        second_task = asyncio.create_task(second())
        try:
            await _wait_for_database_blocker(
                observer,
                waiter_pid=terminal_pid if first_lock == "trigger" else trigger_pid,
                blocker_pid=trigger_pid if first_lock == "trigger" else terminal_pid,
            )
        finally:
            release_first.set()
        first_result, second_result = await asyncio.wait_for(
            asyncio.gather(first_task, second_task),
            timeout=10,
        )
        terminal_result = first_result if first_lock == "terminal" else second_result

    async with harness.session_factory() as observer:
        persisted = await observer.get(MatchResult, match.id)
    assert persisted is not None
    assert persisted.status == "PENDING"
    assert persisted.generation == 6
    assert persisted.overall_score is None and persisted.error_message is None
    assert terminal_result is (first_lock == "terminal")
