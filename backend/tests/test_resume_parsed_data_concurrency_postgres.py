from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.core.config import Settings
from app.core.engine_factory import create_engine
from app.core.exceptions import APIError
from app.core.version_guard import claim_resume_revision
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import CandidateProfile, Resume
from app.models.skill import JobSkill, Skill
from app.models.user import User
from app.schemas.job_schema import JobCriteriaRequest
from app.schemas.match_schema import MatchCalculateRequest
from app.schemas.resume_schema import ResumeParsedDataUpdate
from app.services.job_service import update_job_criteria
from app.services.match_dispatcher import NoOpMatchDispatcher
from app.services.match_service import calculate_matches
from app.services.resume_service import soft_delete_resume, update_resume_parsed_data
from app.workers import match_worker


class ConstantProvider:
    model_name = BGE_M3_MODEL_NAME
    dimension = BGE_M3_EMBEDDING_DIMENSION

    def __init__(self, value: float) -> None:
        self.value = value
        self.texts: list[str] = []

    async def embed(self, text_value: str) -> list[float]:
        self.texts.append(text_value)
        return [self.value] * self.dimension


class GatedProvider(ConstantProvider):
    def __init__(self, value: float) -> None:
        super().__init__(value)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def embed(self, text_value: str) -> list[float]:
        self.texts.append(text_value)
        self.entered.set()
        await self.release.wait()
        return [self.value] * self.dimension


@dataclass
class SignalingSessionFactory:
    delegate: async_sessionmaker[AsyncSession]
    entered: asyncio.Event = field(default_factory=asyncio.Event)

    def __call__(self) -> AsyncSession:
        self.entered.set()
        return self.delegate()


@dataclass(frozen=True)
class ParsedDataRaceHarness:
    session_factory: async_sessionmaker[AsyncSession]
    candidate_id: uuid.UUID
    hr_id: uuid.UUID
    resume_id: uuid.UUID
    job_id: uuid.UUID
    second_job_id: uuid.UUID
    match_id: uuid.UUID
    skill_id: int


def _user(role: str, unique: str, name: str) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=f"parsed-race.{unique}.{name}@example.com",
        password_hash="not-used",
        full_name=f"Parsed Race {name}",
        phone_number=None,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _job(unique: str, name: str, recruiter_id: uuid.UUID) -> JobDescription:
    now = datetime.now(UTC)
    return JobDescription(
        id=uuid.uuid4(),
        recruiter_id=recruiter_id,
        title=name,
        job_level="Senior",
        raw_content=f"Job {unique} {name}",
        create_request_fingerprint=uuid.uuid4().hex * 2,
        revision=1,
        min_experience_years=Decimal("0.0"),
        job_embedding=[0.2] * BGE_M3_EMBEDDING_DIMENSION,
        embedding_model=BGE_M3_MODEL_NAME,
        embedding_preprocessing_version="resume-text-v1",
        parsing_status="PARSED",
        parsing_error_message=None,
        is_criteria_verified=True,
        w_skill=Decimal("0.500"),
        w_semantic=Decimal("0.300"),
        w_experience=Decimal("0.200"),
        status="ACTIVE",
        is_deleted=False,
        created_at=now,
        updated_at=now,
        parsed_at=now,
        deleted_at=None,
    )


@pytest_asyncio.fixture
async def parsed_data_race() -> AsyncIterator[ParsedDataRaceHarness]:
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
    now = datetime.now(UTC)
    resume = Resume(
        id=uuid.uuid4(),
        owner_user_id=candidate.id,
        file_name="race.pdf",
        storage_key=f"parsed-race/{unique}/source",
        file_size=128,
        mime_type="application/pdf",
        create_request_fingerprint=uuid.uuid4().hex * 2,
        revision=1,
        parsing_status="PARSED",
        raw_text="original source text",
        resume_embedding=[0.1] * BGE_M3_EMBEDDING_DIMENSION,
        embedding_model=BGE_M3_MODEL_NAME,
        embedding_preprocessing_version="resume-text-v1",
        error_message=None,
        is_manually_edited=False,
        is_deleted=False,
        created_at=now,
        updated_at=now,
        parsed_at=now,
        deleted_at=None,
    )
    job = _job(unique, "primary", hr.id)
    second_job = _job(unique, "second", hr.id)
    match = MatchResult(
        id=uuid.uuid4(),
        job_id=job.id,
        resume_id=resume.id,
        generation=1,
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
        status="PENDING",
        error_message=None,
        created_at=now,
        updated_at=now,
        calculated_at=None,
    )
    tracked_user_ids = (candidate.id, hr.id)
    async with session_factory() as setup:
        skill_id = await setup.scalar(select(Skill.id).order_by(Skill.id.asc()).limit(1))
        if skill_id is None:
            await engine.dispose()
            pytest.skip("The configured database has no canonical skills")
        setup.add_all((candidate, hr))
        await setup.flush()
        setup.add_all((resume, job, second_job))
        await setup.flush()
        setup.add(match)
        await setup.flush()
        setup.add_all(
            (
                JobSkill(
                    job_id=job.id,
                    skill_id=skill_id,
                    importance="MANDATORY",
                    min_years_required=Decimal("1.0"),
                ),
                JobSkill(
                    job_id=second_job.id,
                    skill_id=skill_id,
                    importance="MANDATORY",
                    min_years_required=Decimal("1.0"),
                ),
                CandidateProfile(resume_id=resume.id, full_name="Original Candidate"),
            )
        )
        await setup.commit()

    try:
        yield ParsedDataRaceHarness(
            session_factory,
            candidate.id,
            hr.id,
            resume.id,
            job.id,
            second_job.id,
            match.id,
            skill_id,
        )
    finally:
        async with session_factory() as cleanup:
            await cleanup.execute(delete(User).where(User.id.in_(tracked_user_ids)))
            await cleanup.commit()
        await engine.dispose()


def _payload(marker: str, skill_id: int) -> ResumeParsedDataUpdate:
    return ResumeParsedDataUpdate.model_validate(
        {
            "candidate_profile": {"full_name": marker},
            "skills": [{"skill_id": skill_id, "years_of_experience": 2.0}],
            "experiences": [],
            "educations": [],
        }
    )


async def _actor(session: AsyncSession, actor_id: uuid.UUID) -> User:
    actor = await session.get(User, actor_id)
    assert actor is not None
    return actor


async def _blocked_by(observer: AsyncSession, blocker_pid: int, count: int = 1) -> tuple[int, ...]:
    async with asyncio.timeout(5):
        while True:
            rows = tuple(
                (
                    await observer.scalars(
                        text(
                            "SELECT pid FROM pg_stat_activity "
                            "WHERE :blocker_pid = ANY(pg_blocking_pids(pid)) ORDER BY pid"
                        ),
                        {"blocker_pid": blocker_pid},
                    )
                ).all()
            )
            if len(rows) >= count:
                return rows


async def _blocked_sessions(
    observer: AsyncSession,
    *,
    count: int,
) -> tuple[tuple[int, tuple[int, ...]], ...]:
    async with asyncio.timeout(5):
        while True:
            rows = tuple(
                (
                    pid,
                    tuple(blockers),
                )
                for pid, blockers in (
                    await observer.execute(
                        text(
                            "SELECT pid, pg_blocking_pids(pid) FROM pg_stat_activity "
                            "WHERE cardinality(pg_blocking_pids(pid)) > 0 ORDER BY pid"
                        )
                    )
                ).all()
            )
            if len(rows) >= count:
                return rows


def _terminal_values(status: str) -> dict[str, object]:
    now = datetime.now(UTC)
    completed = status == "COMPLETED"
    return {
        "status": status,
        "overall_score": Decimal("80.00") if completed else None,
        "skill_score": Decimal("80.00") if completed else None,
        "semantic_score": Decimal("80.00") if completed else None,
        "experience_score": Decimal("80.00") if completed else None,
        "matched_skills": [{"skill_id": 1}] if completed else [],
        "missing_skills": [],
        "gap_analysis_summary": "terminal" if completed else None,
        "embedding_model": BGE_M3_MODEL_NAME if completed else None,
        "embedding_preprocessing_version": "resume-text-v1" if completed else None,
        "error_message": None if completed else "terminal failed",
        "calculated_at": now if completed else None,
        "updated_at": now,
    }


@pytest.mark.asyncio
async def test_concurrent_edits_serialize_from_current_revision_and_generation(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with (
        harness.session_factory() as blocker,
        harness.session_factory() as first,
        harness.session_factory() as second,
        harness.session_factory() as observer,
    ):
        assert await blocker.scalar(
            select(Resume).where(Resume.id == harness.resume_id).with_for_update()
        )
        blocker_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        assert blocker_pid is not None
        first_actor = await _actor(first, harness.candidate_id)
        second_actor = await _actor(second, harness.candidate_id)
        first_task = asyncio.create_task(
            update_resume_parsed_data(
                first,
                current_user=first_actor,
                resume_id=harness.resume_id,
                payload=_payload("first", harness.skill_id),
                embedding_provider=ConstantProvider(0.11),
            )
        )
        second_task = asyncio.create_task(
            update_resume_parsed_data(
                second,
                current_user=second_actor,
                resume_id=harness.resume_id,
                payload=_payload("second", harness.skill_id),
                embedding_provider=ConstantProvider(0.22),
            )
        )
        try:
            blocked = await _blocked_sessions(observer, count=2)
            waiter_pids = {pid for pid, _ in blocked}
            assert len(waiter_pids) >= 2
            assert blocker_pid in {pid for _, blockers in blocked for pid in blockers}
            await blocker.commit()
            results = await asyncio.wait_for(
                asyncio.gather(first_task, second_task),
                timeout=10,
            )
        finally:
            if blocker.in_transaction():
                await blocker.rollback()
            for task in (first_task, second_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(first_task, second_task, return_exceptions=True)

    assert {result.resume.revision for result in results} == {2, 3}
    async with harness.session_factory() as verify:
        resume = await verify.get(Resume, harness.resume_id)
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == harness.resume_id)
        )
        match = await verify.get(MatchResult, harness.match_id)
    assert resume is not None and profile is not None and match is not None
    assert resume.revision == 3 and match.generation == 3 and match.resume_revision == 3
    expected_value = 0.11 if profile.full_name == "first" else 0.22
    assert all(abs(value - expected_value) < 1e-6 for value in (resume.resume_embedding or []))


@pytest.mark.asyncio
async def test_soft_delete_during_inference_wins_without_partial_edit(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    provider = GatedProvider(0.3)
    async with (
        harness.session_factory() as editor,
        harness.session_factory() as deleter,
    ):
        editor_actor = await _actor(editor, harness.candidate_id)
        deleter_actor = await _actor(deleter, harness.candidate_id)
        edit_task = asyncio.create_task(
            update_resume_parsed_data(
                editor,
                current_user=editor_actor,
                resume_id=harness.resume_id,
                payload=_payload("must-not-persist", harness.skill_id),
                embedding_provider=provider,
            )
        )
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        await soft_delete_resume(
            deleter,
            current_user=deleter_actor,
            resume_id=harness.resume_id,
        )
        provider.release.set()
        with pytest.raises(APIError) as raised:
            await asyncio.wait_for(edit_task, timeout=10)
    assert raised.value.status_code == 404

    async with harness.session_factory() as verify:
        resume = await verify.get(Resume, harness.resume_id)
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == harness.resume_id)
        )
        match = await verify.get(MatchResult, harness.match_id)
    assert resume is not None and profile is not None and match is not None
    assert resume.is_deleted is True and resume.revision == 1
    assert profile.full_name == "Original Candidate"
    assert match.generation == 1


@pytest.mark.asyncio
async def test_editor_commit_then_waiting_soft_delete_preserves_edit_revision(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with (
        harness.session_factory() as match_blocker,
        harness.session_factory() as editor,
        harness.session_factory() as deleter,
        harness.session_factory() as observer,
    ):
        assert await match_blocker.scalar(
            select(MatchResult).where(MatchResult.id == harness.match_id).with_for_update()
        )
        blocker_pid = await match_blocker.scalar(text("SELECT pg_backend_pid()"))
        assert blocker_pid is not None
        editor_actor = await _actor(editor, harness.candidate_id)
        deleter_actor = await _actor(deleter, harness.candidate_id)
        edit_task = asyncio.create_task(
            update_resume_parsed_data(
                editor,
                current_user=editor_actor,
                resume_id=harness.resume_id,
                payload=_payload("committed-before-delete", harness.skill_id),
                embedding_provider=ConstantProvider(0.35),
            )
        )
        delete_task: asyncio.Task[None] | None = None
        try:
            editor_pid = (await _blocked_by(observer, blocker_pid))[0]
            delete_task = asyncio.create_task(
                soft_delete_resume(
                    deleter,
                    current_user=deleter_actor,
                    resume_id=harness.resume_id,
                )
            )
            assert await _blocked_by(observer, editor_pid)
            await match_blocker.commit()
            await asyncio.wait_for(asyncio.gather(edit_task, delete_task), timeout=10)
        finally:
            if match_blocker.in_transaction():
                await match_blocker.rollback()
            tasks = (edit_task, delete_task)
            for task in tasks:
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in tasks if task is not None),
                return_exceptions=True,
            )

    async with harness.session_factory() as verify:
        resume = await verify.get(Resume, harness.resume_id)
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == harness.resume_id)
        )
        match = await verify.get(MatchResult, harness.match_id)
    assert resume is not None and profile is not None and match is not None
    assert resume.is_deleted is True and resume.revision == 2
    assert profile.full_name == "committed-before-delete"
    assert match.generation == 2 and match.resume_revision == 2


@pytest.mark.asyncio
async def test_trigger_committed_during_inference_is_discovered_and_invalidated(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    provider = GatedProvider(0.4)
    async with (
        harness.session_factory() as editor,
        harness.session_factory() as trigger,
    ):
        editor_actor = await _actor(editor, harness.candidate_id)
        trigger_actor = await _actor(trigger, harness.candidate_id)
        edit_task = asyncio.create_task(
            update_resume_parsed_data(
                editor,
                current_user=editor_actor,
                resume_id=harness.resume_id,
                payload=_payload("after-trigger", harness.skill_id),
                embedding_provider=provider,
            )
        )
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        match_ids = await calculate_matches(
            trigger,
            current_user=trigger_actor,
            payload=MatchCalculateRequest(
                job_id=harness.second_job_id,
                resume_ids=[harness.resume_id],
            ),
            dispatcher=NoOpMatchDispatcher(),
        )
        provider.release.set()
        await asyncio.wait_for(edit_task, timeout=10)

    async with harness.session_factory() as verify:
        created = await verify.get(MatchResult, match_ids[0])
    assert created is not None
    assert created.generation == 2
    assert created.resume_revision == 2
    assert created.status == "PENDING"


@pytest.mark.asyncio
async def test_editor_then_trigger_wait_graph_completes_without_deadlock(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with (
        harness.session_factory() as match_blocker,
        harness.session_factory() as editor,
        harness.session_factory() as trigger,
        harness.session_factory() as observer,
    ):
        assert await match_blocker.scalar(
            select(MatchResult).where(MatchResult.id == harness.match_id).with_for_update()
        )
        blocker_pid = await match_blocker.scalar(text("SELECT pg_backend_pid()"))
        assert blocker_pid is not None
        editor_actor = await _actor(editor, harness.candidate_id)
        trigger_actor = await _actor(trigger, harness.candidate_id)
        edit_task = asyncio.create_task(
            update_resume_parsed_data(
                editor,
                current_user=editor_actor,
                resume_id=harness.resume_id,
                payload=_payload("editor-first", harness.skill_id),
                embedding_provider=ConstantProvider(0.5),
            )
        )
        trigger_task: asyncio.Task[list[uuid.UUID]] | None = None
        try:
            editor_pid = (await _blocked_by(observer, blocker_pid))[0]
            trigger_task = asyncio.create_task(
                calculate_matches(
                    trigger,
                    current_user=trigger_actor,
                    payload=MatchCalculateRequest(
                        job_id=harness.job_id,
                        resume_ids=[harness.resume_id],
                    ),
                    dispatcher=NoOpMatchDispatcher(),
                )
            )
            assert await _blocked_by(observer, editor_pid)
            await match_blocker.commit()
            await asyncio.wait_for(asyncio.gather(edit_task, trigger_task), timeout=10)
        finally:
            if match_blocker.in_transaction():
                await match_blocker.rollback()
            tasks = (edit_task, trigger_task)
            for task in tasks:
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in tasks if task is not None),
                return_exceptions=True,
            )

    async with harness.session_factory() as verify:
        resume = await verify.get(Resume, harness.resume_id)
        match = await verify.get(MatchResult, harness.match_id)
    assert resume is not None and match is not None
    assert resume.revision == 2
    assert match.generation == 3
    assert match.resume_revision == 2
    assert match.job_revision == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["COMPLETED", "FAILED"])
async def test_match_terminal_commit_during_inference_is_then_invalidated(
    parsed_data_race: ParsedDataRaceHarness,
    terminal_status: str,
) -> None:
    harness = parsed_data_race
    provider = GatedProvider(0.55)
    async with harness.session_factory() as setup:
        await setup.execute(
            update(MatchResult)
            .where(MatchResult.id == harness.match_id)
            .values(status="PROCESSING")
        )
        await setup.commit()
    async with harness.session_factory() as editor:
        actor = await _actor(editor, harness.candidate_id)
        edit_task = asyncio.create_task(
            update_resume_parsed_data(
                editor,
                current_user=actor,
                resume_id=harness.resume_id,
                payload=_payload("after-terminal", harness.skill_id),
                embedding_provider=provider,
            )
        )
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        terminal = await match_worker._terminal_update(
            harness.session_factory,
            match_id=harness.match_id,
            expected_generation=1,
            expected_resume_revision=1,
            expected_job_revision=1,
            algorithm_version="hybrid-v1",
            values=_terminal_values(terminal_status),
        )
        assert terminal is True
        provider.release.set()
        await asyncio.wait_for(edit_task, timeout=10)

    async with harness.session_factory() as verify:
        match = await verify.get(MatchResult, harness.match_id)
    assert match is not None
    assert match.generation == 2 and match.status == "PENDING"
    assert match.resume_revision == 2
    assert match.overall_score is None and match.error_message is None


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["COMPLETED", "FAILED"])
async def test_editor_commit_makes_waiting_match_terminal_stale(
    parsed_data_race: ParsedDataRaceHarness,
    terminal_status: str,
) -> None:
    harness = parsed_data_race
    async with harness.session_factory() as setup:
        await setup.execute(
            update(MatchResult)
            .where(MatchResult.id == harness.match_id)
            .values(status="PROCESSING")
        )
        await setup.commit()
    async with (
        harness.session_factory() as match_blocker,
        harness.session_factory() as editor,
        harness.session_factory() as observer,
    ):
        assert await match_blocker.scalar(
            select(MatchResult).where(MatchResult.id == harness.match_id).with_for_update()
        )
        blocker_pid = await match_blocker.scalar(text("SELECT pg_backend_pid()"))
        assert blocker_pid is not None
        actor = await _actor(editor, harness.candidate_id)
        edit_task = asyncio.create_task(
            update_resume_parsed_data(
                editor,
                current_user=actor,
                resume_id=harness.resume_id,
                payload=_payload("before-terminal", harness.skill_id),
                embedding_provider=ConstantProvider(0.56),
            )
        )
        terminal_task: asyncio.Task[bool] | None = None
        try:
            editor_waiter_pids = await _blocked_by(observer, blocker_pid)
            assert len(editor_waiter_pids) == 1
            terminal_sessions = SignalingSessionFactory(harness.session_factory)
            terminal_task = asyncio.create_task(
                match_worker._terminal_update(
                    terminal_sessions,
                    match_id=harness.match_id,
                    expected_generation=1,
                    expected_resume_revision=1,
                    expected_job_revision=1,
                    algorithm_version="hybrid-v1",
                    values=_terminal_values(terminal_status),
                )
            )
            await asyncio.wait_for(terminal_sessions.entered.wait(), timeout=5)
            assert not terminal_task.done()
            await match_blocker.commit()
            _, terminal = await asyncio.wait_for(
                asyncio.gather(edit_task, terminal_task),
                timeout=10,
            )
        finally:
            if match_blocker.in_transaction():
                await match_blocker.rollback()
            tasks = (edit_task, terminal_task)
            for task in tasks:
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in tasks if task is not None),
                return_exceptions=True,
            )
    assert terminal is False
    async with harness.session_factory() as verify:
        match = await verify.get(MatchResult, harness.match_id)
    assert match is not None
    assert match.generation == 2 and match.status == "PENDING"
    assert match.resume_revision == 2
    assert match.overall_score is None and match.error_message is None


@pytest.mark.asyncio
async def test_criteria_commit_during_inference_is_reflected_by_editor(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    provider = GatedProvider(0.6)
    criteria_payload = JobCriteriaRequest.model_validate(
        {
            "skills": [
                {
                    "skill_id": harness.skill_id,
                    "importance": "MANDATORY",
                    "min_years_required": 2.0,
                }
            ]
        }
    )
    async with (
        harness.session_factory() as editor,
        harness.session_factory() as criteria_session,
    ):
        editor_actor = await _actor(editor, harness.candidate_id)
        criteria_actor = await _actor(criteria_session, harness.hr_id)
        edit_task = asyncio.create_task(
            update_resume_parsed_data(
                editor,
                current_user=editor_actor,
                resume_id=harness.resume_id,
                payload=_payload("after-criteria", harness.skill_id),
                embedding_provider=provider,
            )
        )
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        await update_job_criteria(
            criteria_session,
            current_user=criteria_actor,
            job_id=harness.job_id,
            payload=criteria_payload,
        )
        provider.release.set()
        await asyncio.wait_for(edit_task, timeout=10)

    async with harness.session_factory() as verify:
        job = await verify.get(JobDescription, harness.job_id)
        match = await verify.get(MatchResult, harness.match_id)
    assert job is not None and match is not None
    assert job.revision == 2
    assert match.generation == 3
    assert match.resume_revision == 2
    assert match.job_revision == 2


@pytest.mark.asyncio
async def test_editor_then_criteria_wait_graph_refreshes_both_snapshots(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    criteria_payload = JobCriteriaRequest.model_validate(
        {
            "skills": [
                {
                    "skill_id": harness.skill_id,
                    "importance": "MANDATORY",
                    "min_years_required": 3.0,
                }
            ]
        }
    )
    async with (
        harness.session_factory() as match_blocker,
        harness.session_factory() as editor,
        harness.session_factory() as criteria_session,
        harness.session_factory() as observer,
    ):
        assert await match_blocker.scalar(
            select(MatchResult).where(MatchResult.id == harness.match_id).with_for_update()
        )
        blocker_pid = await match_blocker.scalar(text("SELECT pg_backend_pid()"))
        assert blocker_pid is not None
        editor_actor = await _actor(editor, harness.candidate_id)
        criteria_actor = await _actor(criteria_session, harness.hr_id)
        edit_task = asyncio.create_task(
            update_resume_parsed_data(
                editor,
                current_user=editor_actor,
                resume_id=harness.resume_id,
                payload=_payload("editor-before-criteria", harness.skill_id),
                embedding_provider=ConstantProvider(0.65),
            )
        )
        criteria_task: asyncio.Task[object] | None = None
        try:
            editor_pid = (await _blocked_by(observer, blocker_pid))[0]
            criteria_task = asyncio.create_task(
                update_job_criteria(
                    criteria_session,
                    current_user=criteria_actor,
                    job_id=harness.job_id,
                    payload=criteria_payload,
                )
            )
            assert await _blocked_by(observer, editor_pid)
            await match_blocker.commit()
            await asyncio.wait_for(asyncio.gather(edit_task, criteria_task), timeout=10)
        finally:
            if match_blocker.in_transaction():
                await match_blocker.rollback()
            tasks = (edit_task, criteria_task)
            for task in tasks:
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in tasks if task is not None),
                return_exceptions=True,
            )

    async with harness.session_factory() as verify:
        resume = await verify.get(Resume, harness.resume_id)
        job = await verify.get(JobDescription, harness.job_id)
        match = await verify.get(MatchResult, harness.match_id)
    assert resume is not None and job is not None and match is not None
    assert resume.revision == 2 and job.revision == 2
    assert match.generation == 3
    assert match.resume_revision == 2 and match.job_revision == 2


@pytest.mark.asyncio
async def test_locking_reads_refresh_preloaded_stale_identity_map_objects(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with (
        harness.session_factory() as editor,
        harness.session_factory() as external,
    ):
        actor = await _actor(editor, harness.candidate_id)
        cached_resume = await editor.get(Resume, harness.resume_id)
        cached_match = await editor.get(MatchResult, harness.match_id)
        assert cached_resume is not None and cached_match is not None
        assert (cached_resume.revision, cached_match.generation) == (1, 1)

        await external.execute(
            update(Resume).where(Resume.id == harness.resume_id).values(revision=4)
        )
        await external.execute(
            update(JobDescription).where(JobDescription.id == harness.job_id).values(revision=3)
        )
        await external.execute(
            update(MatchResult)
            .where(MatchResult.id == harness.match_id)
            .values(generation=6, resume_revision=4, job_revision=3)
        )
        await external.commit()

        result = await update_resume_parsed_data(
            editor,
            current_user=actor,
            resume_id=harness.resume_id,
            payload=_payload("fresh-current-state", harness.skill_id),
            embedding_provider=ConstantProvider(0.75),
        )
        assert result.resume is cached_resume
        assert (cached_resume.revision, cached_match.generation) == (5, 7)
        assert cached_match.resume_revision == 5 and cached_match.job_revision == 3


@pytest.mark.asyncio
async def test_flush_failure_rolls_back_aggregate_revision_and_invalidation(
    parsed_data_race: ParsedDataRaceHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = parsed_data_race
    async with harness.session_factory() as editor:
        actor = await _actor(editor, harness.candidate_id)

        async def fail_flush() -> None:
            raise RuntimeError("forced flush failure")

        monkeypatch.setattr(editor, "flush", fail_flush)
        with pytest.raises(RuntimeError, match="forced flush failure"):
            await update_resume_parsed_data(
                editor,
                current_user=actor,
                resume_id=harness.resume_id,
                payload=_payload("must-roll-back", harness.skill_id),
                embedding_provider=ConstantProvider(0.8),
            )

    async with harness.session_factory() as verify:
        resume = await verify.get(Resume, harness.resume_id)
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == harness.resume_id)
        )
        match = await verify.get(MatchResult, harness.match_id)
    assert resume is not None and profile is not None and match is not None
    assert resume.revision == 1 and resume.is_manually_edited is False
    assert profile.full_name == "Original Candidate"
    assert match.generation == 1 and match.resume_revision == 1


@pytest.mark.asyncio
async def test_old_parse_claim_is_stale_after_manual_edit(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with harness.session_factory() as editor:
        actor = await _actor(editor, harness.candidate_id)
        await update_resume_parsed_data(
            editor,
            current_user=actor,
            resume_id=harness.resume_id,
            payload=_payload("new-revision", harness.skill_id),
            embedding_provider=ConstantProvider(0.7),
        )
    async with harness.session_factory() as stale_worker:
        claimed = await claim_resume_revision(
            stale_worker,
            resume_id=harness.resume_id,
            expected_revision=1,
        )
    assert claimed is False
