from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.schemas import ParsedProfile, ParsedResumeResult
from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.core.config import Settings
from app.core.engine_factory import create_engine
from app.core.exceptions import APIError
from app.core.version_guard import claim_resume_revision, fail_resume_revision
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import CandidateProfile, Resume
from app.models.skill import JobSkill, Skill
from app.models.user import User
from app.schemas.job_schema import JobCriteriaRequest
from app.schemas.match_schema import MatchCalculateRequest
from app.schemas.resume_schema import ResumeParsedDataUpdate
from app.services.job_service import update_job_criteria
from app.services.match_recovery import select_match_recovery_candidates
from app.services.match_service import calculate_matches
from app.services.resume_service import soft_delete_resume, update_resume_parsed_data
from app.workers import match_worker, resume_parse_worker


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


class RecordingMatchDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, int, int, int, str]] = []

    async def dispatch(
        self,
        match_id: uuid.UUID,
        expected_generation: int,
        expected_resume_revision: int,
        expected_job_revision: int,
        algorithm_version: str,
    ) -> None:
        self.calls.append(
            (
                match_id,
                expected_generation,
                expected_resume_revision,
                expected_job_revision,
                algorithm_version,
            )
        )


class InvalidProvider(ConstantProvider):
    def __init__(self, kind: str) -> None:
        super().__init__(0.9)
        self.kind = kind
        if kind == "model":
            self.model_name = "incompatible-model"
        if kind == "metadata_dimension":
            self.dimension = BGE_M3_EMBEDDING_DIMENSION - 1

    async def embed(self, text_value: str) -> list[float]:
        self.texts.append(text_value)
        if self.kind == "exception":
            raise RuntimeError("private provider details")
        if self.kind == "wrong_vector_dimension":
            return [self.value] * (BGE_M3_EMBEDDING_DIMENSION - 1)
        if self.kind == "nonfinite":
            return [float("inf"), *([self.value] * (BGE_M3_EMBEDDING_DIMENSION - 1))]
        return [self.value] * self.dimension


class BoundaryStaleProvider(ConstantProvider):
    """Create unexpired stale identities after the service preflight rollback."""

    def __init__(
        self,
        value: float,
        *,
        editor: AsyncSession,
        external: AsyncSession,
        resume_id: uuid.UUID,
        match_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> None:
        super().__init__(value)
        self.editor = editor
        self.external = external
        self.resume_id = resume_id
        self.match_id = match_id
        self.job_id = job_id
        self.strong_references: list[object] = []

    async def embed(self, text_value: str) -> list[float]:
        self.texts.append(text_value)
        cached_resume = await self.editor.get(Resume, self.resume_id)
        cached_match = await self.editor.get(MatchResult, self.match_id)
        cached_job = await self.editor.get(JobDescription, self.job_id)
        assert cached_resume is not None and cached_match is not None and cached_job is not None
        assert (cached_resume.revision, cached_match.generation, cached_job.revision) == (1, 1, 1)
        self.strong_references = [cached_resume, cached_match, cached_job]

        await self.external.execute(
            update(Resume).where(Resume.id == self.resume_id).values(revision=4)
        )
        await self.external.execute(
            update(JobDescription).where(JobDescription.id == self.job_id).values(revision=3)
        )
        await self.external.execute(
            update(MatchResult)
            .where(MatchResult.id == self.match_id)
            .values(generation=6, resume_revision=4, job_revision=3)
        )
        await self.external.commit()
        assert (cached_resume.revision, cached_match.generation, cached_job.revision) == (1, 1, 1)
        return [self.value] * self.dimension


@dataclass
class SignalingSessionFactory:
    delegate: async_sessionmaker[AsyncSession]
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    pid: int | None = None

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        async with self.delegate() as session:
            self.pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert self.pid is not None
            self.entered.set()
            yield session

    def __call__(self) -> object:
        return self._session()


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


@dataclass(frozen=True)
class CriteriaTopology:
    secondary_resume_id: uuid.UUID
    secondary_resume_match_id: uuid.UUID
    second_job_match_id: uuid.UUID


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
            pytest.fail(
                "Configured PostgreSQL database is missing the canonical skill taxonomy seed",
                pytrace=False,
            )
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


async def _waiter_blockers(observer: AsyncSession, waiter_pid: int) -> tuple[int, ...]:
    async with asyncio.timeout(5):
        while True:
            blockers = await observer.scalar(
                text("SELECT pg_blocking_pids(:waiter_pid)"),
                {"waiter_pid": waiter_pid},
            )
            if blockers:
                return tuple(blockers)


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


async def _create_criteria_topology(
    session: AsyncSession,
    harness: ParsedDataRaceHarness,
) -> CriteriaTopology:
    now = datetime.now(UTC)
    maximum_uuid = (1 << 128) - 1
    if harness.resume_id.int < maximum_uuid and harness.match_id.int > 0:
        secondary_resume_id = uuid.UUID(int=harness.resume_id.int + 1)
        secondary_resume_match_id = uuid.UUID(int=harness.match_id.int - 1)
    else:
        secondary_resume_id = uuid.UUID(int=harness.resume_id.int - 1)
        secondary_resume_match_id = uuid.UUID(int=harness.match_id.int + 1)
    assert (harness.resume_id < secondary_resume_id) != (
        harness.match_id < secondary_resume_match_id
    )
    second_job_match_id = uuid.uuid4()
    secondary_resume = Resume(
        id=secondary_resume_id,
        owner_user_id=harness.candidate_id,
        file_name="criteria-secondary.pdf",
        storage_key=f"parsed-race/{secondary_resume_id}/source",
        file_size=128,
        mime_type="application/pdf",
        create_request_fingerprint=uuid.uuid4().hex * 2,
        revision=1,
        parsing_status="PARSED",
        raw_text="secondary resume",
        resume_embedding=[0.15] * BGE_M3_EMBEDDING_DIMENSION,
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

    def completed_match(
        match_id: uuid.UUID,
        *,
        resume_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> MatchResult:
        return MatchResult(
            id=match_id,
            job_id=job_id,
            resume_id=resume_id,
            generation=1,
            resume_revision=1,
            job_revision=1,
            algorithm_version="hybrid-v1",
            created_at=now,
            **_terminal_values("COMPLETED"),
        )

    session.add(secondary_resume)
    await session.flush()
    session.add_all(
        (
            completed_match(
                secondary_resume_match_id,
                resume_id=secondary_resume_id,
                job_id=harness.job_id,
            ),
            completed_match(
                second_job_match_id,
                resume_id=harness.resume_id,
                job_id=harness.second_job_id,
            ),
        )
    )
    await session.execute(
        update(MatchResult)
        .where(MatchResult.id == harness.match_id)
        .values(**_terminal_values("COMPLETED"))
    )
    await session.commit()
    return CriteriaTopology(
        secondary_resume_id,
        secondary_resume_match_id,
        second_job_match_id,
    )


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
        try:
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
        finally:
            provider.release.set()
            if not edit_task.done():
                edit_task.cancel()
            await asyncio.gather(edit_task, return_exceptions=True)

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
async def test_current_nonparsed_state_during_inference_wins_without_partial_edit(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    provider = GatedProvider(0.31)
    async with (
        harness.session_factory() as editor,
        harness.session_factory() as external,
    ):
        actor = await _actor(editor, harness.candidate_id)
        edit_task = asyncio.create_task(
            update_resume_parsed_data(
                editor,
                current_user=actor,
                resume_id=harness.resume_id,
                payload=_payload("must-not-persist", harness.skill_id),
                embedding_provider=provider,
            )
        )
        try:
            await asyncio.wait_for(provider.entered.wait(), timeout=5)
            await external.execute(
                update(Resume)
                .where(Resume.id == harness.resume_id)
                .values(parsing_status="PENDING")
            )
            await external.commit()
            provider.release.set()
            with pytest.raises(APIError) as raised:
                await asyncio.wait_for(edit_task, timeout=10)
            assert raised.value.status_code == 422
        finally:
            provider.release.set()
            if not edit_task.done():
                edit_task.cancel()
            await asyncio.gather(edit_task, return_exceptions=True)

    async with harness.session_factory() as verify:
        resume = await verify.get(Resume, harness.resume_id)
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == harness.resume_id)
        )
        match = await verify.get(MatchResult, harness.match_id)
    assert resume is not None and profile is not None and match is not None
    assert resume.parsing_status == "PENDING" and resume.revision == 1
    assert profile.full_name == "Original Candidate" and match.generation == 1


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
@pytest.mark.parametrize("existing_pair", [True, False])
async def test_trigger_committed_during_inference_is_discovered_and_invalidated(
    parsed_data_race: ParsedDataRaceHarness,
    existing_pair: bool,
) -> None:
    harness = parsed_data_race
    provider = GatedProvider(0.4)
    dispatcher = RecordingMatchDispatcher()
    target_job_id = harness.job_id if existing_pair else harness.second_job_id
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
        try:
            await asyncio.wait_for(provider.entered.wait(), timeout=5)
            match_ids = await calculate_matches(
                trigger,
                current_user=trigger_actor,
                payload=MatchCalculateRequest(
                    job_id=target_job_id,
                    resume_ids=[harness.resume_id],
                ),
                dispatcher=dispatcher,
            )
            old_payload = dispatcher.calls[-1]
            provider.release.set()
            await asyncio.wait_for(edit_task, timeout=10)

            stale_outcome = await match_worker.process_match_task(
                *old_payload,
                session_factory=harness.session_factory,
            )
            assert stale_outcome.value == "DISCARDED"
            await calculate_matches(
                trigger,
                current_user=trigger_actor,
                payload=MatchCalculateRequest(
                    job_id=target_job_id,
                    resume_ids=[harness.resume_id],
                ),
                dispatcher=dispatcher,
            )
        finally:
            provider.release.set()
            if not edit_task.done():
                edit_task.cancel()
            await asyncio.gather(edit_task, return_exceptions=True)

    async with harness.session_factory() as verify:
        created = await verify.get(MatchResult, match_ids[0])
    assert created is not None
    expected_old_generation = 2 if existing_pair else 1
    assert old_payload == (
        created.id,
        expected_old_generation,
        1,
        1,
        "hybrid-v1",
    )
    assert dispatcher.calls[-1] == (
        created.id,
        expected_old_generation + 2,
        2,
        1,
        "hybrid-v1",
    )
    assert created.generation == expected_old_generation + 2
    assert created.resume_revision == 2
    assert created.status == "PENDING"


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_pair", [True, False])
async def test_editor_then_trigger_wait_graph_completes_without_deadlock(
    parsed_data_race: ParsedDataRaceHarness,
    existing_pair: bool,
) -> None:
    harness = parsed_data_race
    dispatcher = RecordingMatchDispatcher()
    target_job_id = harness.job_id if existing_pair else harness.second_job_id
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
                        job_id=target_job_id,
                        resume_ids=[harness.resume_id],
                    ),
                    dispatcher=dispatcher,
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
        match = await verify.scalar(
            select(MatchResult).where(
                MatchResult.resume_id == harness.resume_id,
                MatchResult.job_id == target_job_id,
            )
        )
    assert resume is not None and match is not None
    assert resume.revision == 2
    assert match.generation == (3 if existing_pair else 1)
    assert match.resume_revision == 2
    assert match.job_revision == 1
    assert dispatcher.calls == [(match.id, match.generation, 2, 1, "hybrid-v1")]


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
    async with (
        harness.session_factory() as editor,
        harness.session_factory() as resource_blocker,
    ):
        assert await resource_blocker.scalar(
            select(Resume).where(Resume.id == harness.resume_id).with_for_update()
        )
        blocker_pid = await resource_blocker.scalar(text("SELECT pg_backend_pid()"))
        assert blocker_pid is not None
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
        terminal_task: asyncio.Task[bool] | None = None
        try:
            await asyncio.wait_for(provider.entered.wait(), timeout=5)
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
            assert terminal_sessions.pid is not None
            assert blocker_pid in await _waiter_blockers(resource_blocker, terminal_sessions.pid)
            await resource_blocker.commit()
            terminal = await asyncio.wait_for(terminal_task, timeout=10)
            assert terminal is True
            provider.release.set()
            await asyncio.wait_for(edit_task, timeout=10)
        finally:
            provider.release.set()
            if resource_blocker.in_transaction():
                await resource_blocker.rollback()
            for task in (edit_task, terminal_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (edit_task, terminal_task) if task is not None),
                return_exceptions=True,
            )

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
            assert terminal_sessions.pid is not None
            blockers = await _waiter_blockers(observer, terminal_sessions.pid)
            assert editor_waiter_pids[0] in blockers
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
        try:
            await asyncio.wait_for(provider.entered.wait(), timeout=5)
            await update_job_criteria(
                criteria_session,
                current_user=criteria_actor,
                job_id=harness.job_id,
                payload=criteria_payload,
            )
            provider.release.set()
            await asyncio.wait_for(edit_task, timeout=10)
        finally:
            provider.release.set()
            if not edit_task.done():
                edit_task.cancel()
            await asyncio.gather(edit_task, return_exceptions=True)

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


async def _assert_cross_job_criteria_outcome(
    session: AsyncSession,
    harness: ParsedDataRaceHarness,
    topology: CriteriaTopology,
) -> None:
    resume = await session.get(Resume, harness.resume_id)
    secondary_resume = await session.get(Resume, topology.secondary_resume_id)
    job = await session.get(JobDescription, harness.job_id)
    matches = {
        row.id: row
        for row in (
            await session.scalars(
                select(MatchResult).where(
                    MatchResult.id.in_(
                        (
                            harness.match_id,
                            topology.secondary_resume_match_id,
                            topology.second_job_match_id,
                        )
                    )
                )
            )
        ).all()
    }
    assert resume is not None and secondary_resume is not None and job is not None
    assert resume.revision == 2 and secondary_resume.revision == 1 and job.revision == 2
    primary = matches[harness.match_id]
    secondary = matches[topology.secondary_resume_match_id]
    second_job = matches[topology.second_job_match_id]
    assert (primary.generation, primary.resume_revision, primary.job_revision) == (3, 2, 2)
    assert (secondary.generation, secondary.resume_revision, secondary.job_revision) == (2, 1, 2)
    assert (second_job.generation, second_job.resume_revision, second_job.job_revision) == (2, 2, 1)
    for match in matches.values():
        assert match.status == "PENDING"
        assert match.overall_score is None and match.skill_score is None
        assert match.semantic_score is None and match.experience_score is None
        assert match.matched_skills == [] and match.missing_skills == []
        assert match.gap_analysis_summary is None and match.error_message is None
        assert match.embedding_model is None and match.embedding_preprocessing_version is None
        assert match.calculated_at is None


@pytest.mark.asyncio
async def test_cross_job_criteria_first_lock_graph_has_no_resume_to_job_inversion(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with harness.session_factory() as setup:
        topology = await _create_criteria_topology(setup, harness)
    provider = GatedProvider(0.66)
    criteria_payload = JobCriteriaRequest.model_validate(
        {
            "skills": [
                {
                    "skill_id": harness.skill_id,
                    "importance": "MANDATORY",
                    "min_years_required": 4.0,
                }
            ]
        }
    )
    async with (
        harness.session_factory() as match_blocker,
        harness.session_factory() as editor,
        harness.session_factory() as criteria_session,
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
                payload=_payload("cross-job-criteria-first", harness.skill_id),
                embedding_provider=provider,
            )
        )
        criteria_task: asyncio.Task[object] | None = None
        try:
            await asyncio.wait_for(provider.entered.wait(), timeout=5)
            criteria_task = asyncio.create_task(
                update_job_criteria(
                    criteria_session,
                    current_user=criteria_actor,
                    job_id=harness.job_id,
                    payload=criteria_payload,
                )
            )
            criteria_pid = (await _blocked_by(match_blocker, blocker_pid))[0]
            provider.release.set()
            waits = await _blocked_sessions(match_blocker, count=2)
            wait_graph = {pid: blockers for pid, blockers in waits}
            editor_pids = set(wait_graph) - {criteria_pid}
            assert len(editor_pids) == 1
            editor_pid = editor_pids.pop()
            assert set(wait_graph[editor_pid]) & {criteria_pid, blocker_pid}
            assert wait_graph[criteria_pid] == (blocker_pid,)
            await match_blocker.commit()
            await asyncio.wait_for(asyncio.gather(edit_task, criteria_task), timeout=10)
        finally:
            provider.release.set()
            if match_blocker.in_transaction():
                await match_blocker.rollback()
            for task in (edit_task, criteria_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (edit_task, criteria_task) if task is not None),
                return_exceptions=True,
            )

    async with harness.session_factory() as verify:
        await _assert_cross_job_criteria_outcome(verify, harness, topology)


@pytest.mark.asyncio
async def test_cross_job_editor_first_lock_graph_has_no_resume_to_job_inversion(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with harness.session_factory() as setup:
        topology = await _create_criteria_topology(setup, harness)
    criteria_payload = JobCriteriaRequest.model_validate(
        {
            "skills": [
                {
                    "skill_id": harness.skill_id,
                    "importance": "MANDATORY",
                    "min_years_required": 5.0,
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
                payload=_payload("cross-job-editor-first", harness.skill_id),
                embedding_provider=ConstantProvider(0.67),
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
            criteria_pid = (await _blocked_by(observer, editor_pid))[0]
            editor_blockers = await _waiter_blockers(observer, editor_pid)
            assert blocker_pid in editor_blockers and criteria_pid not in editor_blockers
            await match_blocker.commit()
            await asyncio.wait_for(asyncio.gather(edit_task, criteria_task), timeout=10)
        finally:
            if match_blocker.in_transaction():
                await match_blocker.rollback()
            for task in (edit_task, criteria_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (edit_task, criteria_task) if task is not None),
                return_exceptions=True,
            )

    async with harness.session_factory() as verify:
        await _assert_cross_job_criteria_outcome(verify, harness, topology)


@pytest.mark.asyncio
async def test_preflight_uses_current_parsed_state_not_cached_pending(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with (
        harness.session_factory() as editor,
        harness.session_factory() as external,
    ):
        await external.execute(
            update(Resume)
            .where(Resume.id == harness.resume_id)
            .values(parsing_status="PENDING", revision=1)
        )
        await external.commit()
        cached_resume = await editor.get(Resume, harness.resume_id)
        assert cached_resume is not None and cached_resume.parsing_status == "PENDING"

        await external.execute(
            update(Resume)
            .where(Resume.id == harness.resume_id)
            .values(parsing_status="PARSED", revision=2)
        )
        await external.commit()
        actor = await _actor(editor, harness.candidate_id)
        result = await update_resume_parsed_data(
            editor,
            current_user=actor,
            resume_id=harness.resume_id,
            payload=_payload("current-parsed", harness.skill_id),
            embedding_provider=ConstantProvider(0.71),
        )
        assert result.resume is cached_resume
        assert result.resume.revision == 3

    async with harness.session_factory() as verify:
        persisted = await verify.get(Resume, harness.resume_id)
    assert persisted is not None
    assert persisted.parsing_status == "PARSED" and persisted.revision == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("database_status", ["PENDING", "PROCESSING", "FAILED"])
async def test_preflight_rejects_current_nonparsed_state_not_cached_parsed(
    parsed_data_race: ParsedDataRaceHarness,
    database_status: str,
) -> None:
    harness = parsed_data_race
    provider = ConstantProvider(0.72)
    async with (
        harness.session_factory() as editor,
        harness.session_factory() as external,
    ):
        cached_resume = await editor.get(Resume, harness.resume_id)
        assert cached_resume is not None and cached_resume.parsing_status == "PARSED"
        await external.execute(
            update(Resume)
            .where(Resume.id == harness.resume_id)
            .values(
                parsing_status=database_status,
                error_message="current failure" if database_status == "FAILED" else None,
            )
        )
        await external.commit()
        actor = await _actor(editor, harness.candidate_id)
        with pytest.raises(APIError) as raised:
            await update_resume_parsed_data(
                editor,
                current_user=actor,
                resume_id=harness.resume_id,
                payload=_payload("must-not-persist", harness.skill_id),
                embedding_provider=provider,
            )
        assert raised.value.status_code == 422
        assert cached_resume.parsing_status == "PARSED"
        assert provider.texts == []

    async with harness.session_factory() as verify:
        persisted = await verify.get(Resume, harness.resume_id)
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == harness.resume_id)
        )
        match = await verify.get(MatchResult, harness.match_id)
    assert persisted is not None and profile is not None and match is not None
    assert persisted.parsing_status == database_status and persisted.revision == 1
    assert profile.full_name == "Original Candidate" and match.generation == 1


@pytest.mark.asyncio
async def test_preflight_uses_current_deleted_state_not_cached_visibility(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    provider = ConstantProvider(0.73)
    async with (
        harness.session_factory() as editor,
        harness.session_factory() as external,
    ):
        cached_resume = await editor.get(Resume, harness.resume_id)
        assert cached_resume is not None and cached_resume.is_deleted is False
        await external.execute(
            update(Resume)
            .where(Resume.id == harness.resume_id)
            .values(is_deleted=True, deleted_at=datetime.now(UTC))
        )
        await external.commit()
        actor = await _actor(editor, harness.candidate_id)
        with pytest.raises(APIError) as raised:
            await update_resume_parsed_data(
                editor,
                current_user=actor,
                resume_id=harness.resume_id,
                payload=_payload("must-not-persist", harness.skill_id),
                embedding_provider=provider,
            )
        assert raised.value.status_code == 404
        assert cached_resume.is_deleted is False
        assert provider.texts == []

    async with harness.session_factory() as verify:
        persisted = await verify.get(Resume, harness.resume_id)
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == harness.resume_id)
        )
    assert persisted is not None and persisted.is_deleted is True and persisted.revision == 1
    assert profile is not None and profile.full_name == "Original Candidate"


@pytest.mark.asyncio
async def test_read_only_preflight_does_not_autoflush_unrelated_pending_state(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with harness.session_factory() as editor:
        actor = await _actor(editor, harness.candidate_id)
        actor_email = actor.email
        duplicate_email = _user("CANDIDATE", uuid.uuid4().hex, "unrelated")
        duplicate_email.email = actor_email
        editor.add(duplicate_email)

        result = await update_resume_parsed_data(
            editor,
            current_user=actor,
            resume_id=harness.resume_id,
            payload=_payload("no-autoflush", harness.skill_id),
            embedding_provider=ConstantProvider(0.735),
        )
        assert result.resume.revision == 2

    async with harness.session_factory() as verify:
        duplicate_count = await verify.scalar(
            select(text("count(1)")).select_from(User).where(User.email == actor_email)
        )
    assert duplicate_count == 1


@pytest.mark.asyncio
async def test_final_locking_reads_refresh_unexpired_boundary_identities(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with (
        harness.session_factory() as editor,
        harness.session_factory() as external,
    ):
        actor = await _actor(editor, harness.candidate_id)
        provider = BoundaryStaleProvider(
            0.74,
            editor=editor,
            external=external,
            resume_id=harness.resume_id,
            match_id=harness.match_id,
            job_id=harness.job_id,
        )
        result = await update_resume_parsed_data(
            editor,
            current_user=actor,
            resume_id=harness.resume_id,
            payload=_payload("boundary-current", harness.skill_id),
            embedding_provider=provider,
        )
        cached_resume, cached_match, cached_job = provider.strong_references
        assert result.resume is cached_resume
        assert cached_resume.revision == 5
        assert cached_match.generation == 7
        assert cached_match.resume_revision == 5 and cached_match.job_revision == 3
        # Job is intentionally not refreshed as an ORM entity; the service's
        # scalar projection nevertheless supplied its current revision.
        assert cached_job.revision == 1

    async with harness.session_factory() as verify:
        persisted_resume = await verify.get(Resume, harness.resume_id)
        persisted_match = await verify.get(MatchResult, harness.match_id)
    assert persisted_resume is not None and persisted_match is not None
    assert persisted_resume.revision == 5
    assert persisted_match.generation == 7
    assert persisted_match.resume_revision == 5 and persisted_match.job_revision == 3


@pytest.mark.asyncio
async def test_boundary_regression_is_sensitive_to_populate_existing(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with (
        harness.session_factory() as editor,
        harness.session_factory() as external,
    ):
        actor = await _actor(editor, harness.candidate_id)
        provider = BoundaryStaleProvider(
            0.75,
            editor=editor,
            external=external,
            resume_id=harness.resume_id,
            match_id=harness.match_id,
            job_id=harness.job_id,
        )

        def disable_refresh(execute_state: Any) -> None:
            options = execute_state.execution_options
            if options.get("populate_existing"):
                execute_state.update_execution_options(populate_existing=False)

        event.listen(editor.sync_session, "do_orm_execute", disable_refresh)
        try:
            result = await update_resume_parsed_data(
                editor,
                current_user=actor,
                resume_id=harness.resume_id,
                payload=_payload("refresh-disabled-probe", harness.skill_id),
                embedding_provider=provider,
            )
        finally:
            event.remove(editor.sync_session, "do_orm_execute", disable_refresh)

        cached_resume, cached_match, _ = provider.strong_references
        assert result.resume is cached_resume
        with pytest.raises(AssertionError):
            assert (cached_resume.revision, cached_match.generation) == (5, 7)
        assert (cached_resume.revision, cached_match.generation) == (2, 2)


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
@pytest.mark.parametrize(
    "failure_kind",
    ["exception", "model", "metadata_dimension", "wrong_vector_dimension", "nonfinite"],
)
async def test_provider_failure_preserves_aggregate_embedding_and_stale_match_payload(
    parsed_data_race: ParsedDataRaceHarness,
    failure_kind: str,
) -> None:
    harness = parsed_data_race
    async with harness.session_factory() as setup:
        await setup.execute(
            update(MatchResult)
            .where(MatchResult.id == harness.match_id)
            .values(**_terminal_values("COMPLETED"))
        )
        await setup.commit()

    async with harness.session_factory() as editor:
        actor = await _actor(editor, harness.candidate_id)
        with pytest.raises(APIError) as raised:
            await update_resume_parsed_data(
                editor,
                current_user=actor,
                resume_id=harness.resume_id,
                payload=_payload("must-not-persist", harness.skill_id),
                embedding_provider=InvalidProvider(failure_kind),
            )
        assert raised.value.status_code == 503
        assert raised.value.code == "EMBEDDING_UNAVAILABLE"
        assert raised.value.message == "Resume embedding could not be generated"
        assert "private" not in raised.value.message

    async with harness.session_factory() as verify:
        resume = await verify.get(Resume, harness.resume_id)
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == harness.resume_id)
        )
        match = await verify.get(MatchResult, harness.match_id)
    assert resume is not None and profile is not None and match is not None
    assert resume.revision == 1 and resume.is_manually_edited is False
    assert list(resume.resume_embedding or []) == [0.1] * BGE_M3_EMBEDDING_DIMENSION
    assert profile.full_name == "Original Candidate"
    assert match.generation == 1 and match.resume_revision == 1 and match.job_revision == 1
    assert match.status == "COMPLETED" and match.overall_score == Decimal("80.00")
    assert match.matched_skills == [{"skill_id": 1}]
    assert match.gap_analysis_summary == "terminal"
    assert match.error_message is None
    assert match.embedding_model == BGE_M3_MODEL_NAME
    assert match.embedding_preprocessing_version == "resume-text-v1"
    assert match.calculated_at is not None


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
async def test_commit_failure_before_ack_rolls_back_all_manual_edit_mutations(
    parsed_data_race: ParsedDataRaceHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = parsed_data_race
    async with harness.session_factory() as editor:
        actor = await _actor(editor, harness.candidate_id)

        async def fail_commit() -> None:
            raise RuntimeError("forced commit failure before acknowledgment")

        monkeypatch.setattr(editor, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="forced commit failure"):
            await update_resume_parsed_data(
                editor,
                current_user=actor,
                resume_id=harness.resume_id,
                payload=_payload("must-roll-back-at-commit", harness.skill_id),
                embedding_provider=ConstantProvider(0.83),
            )

    async with harness.session_factory() as verify:
        resume = await verify.get(Resume, harness.resume_id)
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == harness.resume_id)
        )
        match = await verify.get(MatchResult, harness.match_id)
    assert resume is not None and profile is not None and match is not None
    assert resume.revision == 1 and resume.is_manually_edited is False
    assert list(resume.resume_embedding or []) == [0.1] * BGE_M3_EMBEDDING_DIMENSION
    assert profile.full_name == "Original Candidate"
    assert match.generation == 1 and match.resume_revision == 1
    assert match.status == "PENDING" and match.error_message is None


@pytest.mark.asyncio
async def test_recovery_payload_selected_before_edit_is_discarded_and_new_selection_is_current(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    cutoff = datetime.now(UTC) + timedelta(seconds=5)
    before_candidates = await select_match_recovery_candidates(
        harness.session_factory,
        cutoff=cutoff,
        batch_size=100,
    )
    old_candidate = next(item for item in before_candidates if item.match_id == harness.match_id)
    assert (
        old_candidate.expected_generation,
        old_candidate.expected_resume_revision,
        old_candidate.expected_job_revision,
        old_candidate.algorithm_version,
    ) == (1, 1, 1, "hybrid-v1")

    async with harness.session_factory() as editor:
        actor = await _actor(editor, harness.candidate_id)
        await update_resume_parsed_data(
            editor,
            current_user=actor,
            resume_id=harness.resume_id,
            payload=_payload("after-recovery-selection", harness.skill_id),
            embedding_provider=ConstantProvider(0.81),
        )

    old_outcome = await match_worker.process_match_task(
        old_candidate.match_id,
        old_candidate.expected_generation,
        old_candidate.expected_resume_revision,
        old_candidate.expected_job_revision,
        old_candidate.algorithm_version,
        session_factory=harness.session_factory,
    )
    assert old_outcome.value == "DISCARDED"

    async with harness.session_factory() as inspect:
        before_selection = await inspect.get(MatchResult, harness.match_id)
        assert before_selection is not None
        immutable_before = (
            before_selection.generation,
            before_selection.resume_revision,
            before_selection.job_revision,
            before_selection.status,
            before_selection.updated_at,
        )
    after_candidates = await select_match_recovery_candidates(
        harness.session_factory,
        cutoff=cutoff,
        batch_size=100,
    )
    current_candidate = next(item for item in after_candidates if item.match_id == harness.match_id)
    assert (
        current_candidate.expected_generation,
        current_candidate.expected_resume_revision,
        current_candidate.expected_job_revision,
        current_candidate.algorithm_version,
    ) == (2, 2, 1, "hybrid-v1")
    async with harness.session_factory() as verify:
        after_selection = await verify.get(MatchResult, harness.match_id)
    assert after_selection is not None
    assert (
        after_selection.generation,
        after_selection.resume_revision,
        after_selection.job_revision,
        after_selection.status,
        after_selection.updated_at,
    ) == immutable_before


@pytest.mark.asyncio
async def test_old_parse_success_and_failure_terminals_cannot_overwrite_manual_aggregate(
    parsed_data_race: ParsedDataRaceHarness,
) -> None:
    harness = parsed_data_race
    async with harness.session_factory() as editor:
        actor = await _actor(editor, harness.candidate_id)
        await update_resume_parsed_data(
            editor,
            current_user=actor,
            resume_id=harness.resume_id,
            payload=_payload("manual-wins", harness.skill_id),
            embedding_provider=ConstantProvider(0.82),
        )

    stale_success = await resume_parse_worker._persist_success(
        harness.session_factory,
        resume_id=harness.resume_id,
        expected_revision=1,
        parsed=ParsedResumeResult(
            raw_text="stale worker raw text",
            profile=ParsedProfile(full_name="stale worker"),
        ),
        embedding=[0.99] * BGE_M3_EMBEDDING_DIMENSION,
    )
    async with harness.session_factory() as stale_failure_session:
        stale_failure = await fail_resume_revision(
            stale_failure_session,
            resume_id=harness.resume_id,
            expected_revision=1,
            error_message="stale failure",
        )
    assert stale_success is False and stale_failure is False

    async with harness.session_factory() as verify:
        resume = await verify.get(Resume, harness.resume_id)
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == harness.resume_id)
        )
    assert resume is not None and profile is not None
    assert resume.revision == 2 and resume.parsing_status == "PARSED"
    assert resume.raw_text == "original source text"
    assert list(resume.resume_embedding or []) == [0.82] * BGE_M3_EMBEDDING_DIMENSION
    assert resume.error_message is None and profile.full_name == "manual-wins"


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
