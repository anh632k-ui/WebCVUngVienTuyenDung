from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.engine_factory import create_engine
from app.core.exceptions import APIError
from app.core.security import create_access_token
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume
from app.models.skill import JobSkill, Skill
from app.models.user import User
from app.schemas.job_schema import JobData, JobWeightsRequest
from app.services.job_service import update_job_weights
from app.services.match_dispatcher import get_match_dispatcher
from main import app

TEST_SECRET = "job-weights-postgres-jwt-secret-value"


class RecordingDispatcher:
    def __init__(self, *, fail_first: bool = False, pause: bool = False) -> None:
        self.calls: list[tuple[uuid.UUID, int, int, int, str]] = []
        self.fail_first = fail_first
        self.pause = pause
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

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
        if self.pause:
            self.entered.set()
            await self.release.wait()
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("private broker path")


class CommitGateSession(AsyncSession):
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

    async def commit(self) -> None:
        self.entered.set()
        await self.release.wait()
        await super().commit()


class AutoflushEnabledSession:
    """Negative control that removes only the production no-autoflush guard."""

    def __init__(self, delegate: AsyncSession) -> None:
        self.delegate = delegate

    @property
    def no_autoflush(self) -> Any:
        return nullcontext()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


@dataclass(frozen=True)
class Harness:
    client: AsyncClient
    factory: async_sessionmaker[AsyncSession]
    settings: Settings
    dispatcher: RecordingDispatcher
    users: dict[str, uuid.UUID]
    jobs: dict[str, uuid.UUID]
    resumes: tuple[uuid.UUID, ...]
    matches: tuple[uuid.UUID, ...]
    skill_id: int

    def headers(self, user: str) -> dict[str, str]:
        token = create_access_token(self.users[user], self.settings)
        return {"Authorization": f"Bearer {token}"}


def make_user(unique: str, role: str, name: str) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=f"weights.{unique}.{name}@example.com",
        password_hash="not-used",
        full_name=name,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def make_job(
    owner: User,
    name: str,
    parsing_status: str,
    *,
    deleted: bool = False,
) -> JobDescription:
    now = datetime.now(UTC)
    parsed = parsing_status == "PARSED"
    return JobDescription(
        id=uuid.uuid4(),
        recruiter_id=owner.id,
        title=f"Weights {name}",
        job_level="SENIOR",
        location="Hanoi",
        raw_content=f"Raw {name}",
        create_request_fingerprint=hashlib.sha256(name.encode()).hexdigest(),
        revision=7,
        min_experience_years=Decimal("3.0"),
        education_requirement="Bachelor",
        job_embedding=[0.2] * 1024 if parsed else None,
        embedding_model="BAAI/bge-m3" if parsed else None,
        embedding_preprocessing_version="resume-text-v1" if parsed else None,
        parsing_status=parsing_status,
        parsing_error_message="parse failed" if parsing_status == "FAILED" else None,
        is_criteria_verified=parsed,
        w_skill=Decimal("0.500"),
        w_semantic=Decimal("0.300"),
        w_experience=Decimal("0.200"),
        status="ACTIVE" if parsed else "DRAFT",
        is_deleted=deleted,
        created_at=now,
        updated_at=now,
        parsed_at=now if parsed else None,
        deleted_at=now if deleted else None,
    )


def make_resume(owner: User, index: int, *, deleted: bool) -> Resume:
    now = datetime.now(UTC)
    return Resume(
        id=uuid.uuid4(),
        owner_user_id=owner.id,
        file_name=f"weights-{index}.pdf",
        storage_key=f"weights/{uuid.uuid4()}",
        file_size=100 + index,
        mime_type="application/pdf",
        create_request_fingerprint=hashlib.sha256(f"resume-{index}".encode()).hexdigest(),
        revision=3 + index,
        parsing_status="PARSED",
        raw_text=f"Resume {index}",
        resume_embedding=[0.3] * 1024,
        embedding_model="BAAI/bge-m3",
        embedding_preprocessing_version="resume-text-v1",
        error_message=None,
        is_manually_edited=False,
        is_deleted=deleted,
        created_at=now,
        updated_at=now,
        parsed_at=now,
        deleted_at=now if deleted else None,
    )


def make_match(job: JobDescription, resume: Resume, index: int) -> MatchResult:
    now = datetime.now(UTC)
    completed = index == 0
    return MatchResult(
        id=uuid.uuid4(),
        job_id=job.id,
        resume_id=resume.id,
        generation=10 + index,
        resume_revision=resume.revision,
        job_revision=job.revision,
        overall_score=Decimal("80.00") if completed else None,
        skill_score=Decimal("81.00") if completed else None,
        semantic_score=Decimal("82.00") if completed else None,
        experience_score=Decimal("83.00") if completed else None,
        matched_skills=[{"skill_id": 1}] if completed else [],
        missing_skills=[{"skill_id": 2}] if completed else [],
        gap_analysis_summary="old gap" if completed else None,
        algorithm_version="hybrid-v1",
        embedding_model="BAAI/bge-m3" if completed else None,
        embedding_preprocessing_version="resume-text-v1" if completed else None,
        status="COMPLETED" if completed else "FAILED",
        error_message=None if completed else "old failure",
        created_at=now,
        updated_at=now,
        calculated_at=now if completed else None,
    )


@pytest_asyncio.fixture
async def weights_harness() -> AsyncIterator[Harness]:
    configured = Settings()
    if configured.database_url is None:
        pytest.fail("Configured PostgreSQL database is required", pytrace=False)
    database_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=configured.database_url,
        database_connect_timeout_seconds=configured.database_connect_timeout_seconds,
    )
    engine = create_engine(database_settings)
    assert engine is not None
    factory = async_sessionmaker(engine, expire_on_commit=False)
    unique = uuid.uuid4().hex
    users = {
        "hr": make_user(unique, "HR", "hr"),
        "other": make_user(unique, "HR", "other"),
        "admin": make_user(unique, "ADMIN", "admin"),
        "candidate": make_user(unique, "CANDIDATE", "candidate"),
    }
    jobs = {
        "parsed": make_job(users["hr"], "parsed", "PARSED"),
        "pending": make_job(users["hr"], "pending", "PENDING"),
        "processing": make_job(users["hr"], "processing", "PROCESSING"),
        "failed": make_job(users["hr"], "failed", "FAILED"),
        "foreign": make_job(users["other"], "foreign", "PARSED"),
        "deleted": make_job(users["hr"], "deleted", "PARSED", deleted=True),
    }
    resumes = (
        make_resume(users["hr"], 0, deleted=False),
        make_resume(users["candidate"], 1, deleted=True),
    )
    matches = tuple(
        make_match(jobs["parsed"], resume, index) for index, resume in enumerate(resumes)
    )
    async with factory() as setup:
        skill_id = await setup.scalar(select(Skill.id).order_by(Skill.id).limit(1))
        assert skill_id is not None
        setup.add_all(users.values())
        await setup.flush()
        setup.add_all(jobs.values())
        setup.add_all(resumes)
        await setup.flush()
        setup.add(
            JobSkill(
                job_id=jobs["parsed"].id,
                skill_id=skill_id,
                importance="MANDATORY",
                min_years_required=Decimal("2.0"),
            )
        )
        setup.add_all(matches)
        await setup.commit()

    settings = Settings(_env_file=None, app_env="test", jwt_secret_key=TEST_SECRET)
    dispatcher = RecordingDispatcher()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_match_dispatcher] = lambda: dispatcher
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield Harness(
                client=client,
                factory=factory,
                settings=settings,
                dispatcher=dispatcher,
                users={name: row.id for name, row in users.items()},
                jobs={name: row.id for name, row in jobs.items()},
                resumes=tuple(row.id for row in resumes),
                matches=tuple(row.id for row in matches),
                skill_id=skill_id,
            )
    finally:
        app.dependency_overrides.clear()
        async with factory() as cleanup:
            await cleanup.execute(
                delete(User).where(User.id.in_([row.id for row in users.values()]))
            )
            await cleanup.commit()
        await engine.dispose()


def payload(*, recalculate: bool = False) -> dict[str, object]:
    return {
        "w_skill": 0.2,
        "w_semantic": 0.7,
        "w_experience": 0.1,
        "recalculate": recalculate,
    }


async def wait_for_blocker(
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
async def test_weights_http_invalidates_every_match_without_immediate_dispatch(
    weights_harness: Harness,
) -> None:
    harness = weights_harness
    async with harness.factory() as before:
        job = await before.get(JobDescription, harness.jobs["parsed"])
        rows = (
            await before.scalars(
                select(MatchResult).where(MatchResult.job_id == harness.jobs["parsed"])
            )
        ).all()
        assert job is not None
        job_snapshot = (
            job.raw_content,
            job.create_request_fingerprint,
            job.status,
            job.parsing_status,
            job.is_criteria_verified,
            job.job_embedding,
            job.embedding_model,
            job.embedding_preprocessing_version,
            job.parsed_at,
        )
        generations = {row.id: row.generation for row in rows}

    response = await harness.client.put(
        f"/api/v1/jobs/{harness.jobs['parsed']}/weights",
        headers=harness.headers("hr"),
        json=payload(),
    )
    assert response.status_code == 200
    assert response.json()["data"]["revision"] == 8
    assert harness.dispatcher.calls == []

    async with harness.factory() as verify:
        job = await verify.get(JobDescription, harness.jobs["parsed"])
        rows = (
            await verify.scalars(
                select(MatchResult).where(MatchResult.job_id == harness.jobs["parsed"])
            )
        ).all()
        resumes = {row.id: row for row in (await verify.scalars(select(Resume))).all()}
        assert job is not None
        assert (job.w_skill, job.w_semantic, job.w_experience, job.revision) == (
            Decimal("0.200"),
            Decimal("0.700"),
            Decimal("0.100"),
            8,
        )
        assert (
            job.raw_content,
            job.create_request_fingerprint,
            job.status,
            job.parsing_status,
            job.is_criteria_verified,
            job.job_embedding,
            job.embedding_model,
            job.embedding_preprocessing_version,
            job.parsed_at,
        ) == job_snapshot
        for row in rows:
            assert (row.generation, row.resume_revision, row.job_revision, row.status) == (
                generations[row.id] + 1,
                resumes[row.resume_id].revision,
                8,
                "PENDING",
            )
            assert row.overall_score is None and row.skill_score is None
            assert row.semantic_score is None and row.experience_score is None
            assert row.matched_skills == [] and row.missing_skills == []
            assert row.gap_analysis_summary is None and row.error_message is None
            assert row.embedding_model is None and row.embedding_preprocessing_version is None
            assert row.calculated_at is None


@pytest.mark.asyncio
async def test_repeated_weights_update_dispatches_immutable_payloads_and_continues_after_failure(
    weights_harness: Harness,
) -> None:
    harness = weights_harness
    harness.dispatcher.fail_first = True
    response = await harness.client.put(
        f"/api/v1/jobs/{harness.jobs['parsed']}/weights",
        headers=harness.headers("hr"),
        json=payload(recalculate=True),
    )
    assert response.status_code == 200
    assert len(harness.dispatcher.calls) == 2
    assert all(call[3] == 8 and call[4] == "hybrid-v1" for call in harness.dispatcher.calls)

    second = await harness.client.put(
        f"/api/v1/jobs/{harness.jobs['parsed']}/weights",
        headers=harness.headers("hr"),
        json=payload(recalculate=True),
    )
    assert second.status_code == 200
    assert second.json()["data"]["revision"] == 9
    assert len(harness.dispatcher.calls) == 4
    assert all(call[3] == 9 for call in harness.dispatcher.calls[2:])
    assert {call[1] for call in harness.dispatcher.calls[2:]} == {12, 13}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "processing", "failed"])
async def test_weights_rejects_nonparsed_current_state_without_mutation_or_dispatch(
    weights_harness: Harness,
    state: str,
) -> None:
    harness = weights_harness
    response = await harness.client.put(
        f"/api/v1/jobs/{harness.jobs[state]}/weights",
        headers=harness.headers("hr"),
        json=payload(recalculate=True),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "JOB_NOT_READY"
    assert harness.dispatcher.calls == []
    async with harness.factory() as verify:
        job = await verify.get(JobDescription, harness.jobs[state])
        assert job is not None
        assert (job.revision, job.w_skill, job.w_semantic, job.w_experience) == (
            7,
            Decimal("0.500"),
            Decimal("0.300"),
            Decimal("0.200"),
        )


@pytest.mark.asyncio
async def test_weights_enforces_auth_ownership_deleted_and_admin_override(
    weights_harness: Harness,
) -> None:
    harness = weights_harness
    candidate = await harness.client.put(
        f"/api/v1/jobs/{harness.jobs['parsed']}/weights",
        headers=harness.headers("candidate"),
        json=payload(),
    )
    foreign = await harness.client.put(
        f"/api/v1/jobs/{harness.jobs['foreign']}/weights",
        headers=harness.headers("hr"),
        json=payload(),
    )
    deleted = await harness.client.put(
        f"/api/v1/jobs/{harness.jobs['deleted']}/weights",
        headers=harness.headers("admin"),
        json=payload(),
    )
    admin = await harness.client.put(
        f"/api/v1/jobs/{harness.jobs['foreign']}/weights",
        headers=harness.headers("admin"),
        json=payload(),
    )
    assert candidate.status_code == 403
    assert foreign.status_code == deleted.status_code == 404
    assert admin.status_code == 200


@pytest.mark.asyncio
async def test_post_commit_publication_holds_no_job_lock_and_keeps_first_payload(
    weights_harness: Harness,
) -> None:
    harness = weights_harness
    first_dispatcher = RecordingDispatcher(pause=True)
    second_dispatcher = RecordingDispatcher()
    async with harness.factory() as first, harness.factory() as second:
        first_actor = await first.get(User, harness.users["hr"])
        second_actor = await second.get(User, harness.users["hr"])
        assert first_actor is not None and second_actor is not None
        task = asyncio.create_task(
            update_job_weights(
                first,
                current_user=first_actor,
                job_id=harness.jobs["parsed"],
                payload=JobWeightsRequest.model_validate(payload(recalculate=True)),
                dispatcher=first_dispatcher,
            )
        )
        try:
            await asyncio.wait_for(first_dispatcher.entered.wait(), timeout=5)
            second_result = await asyncio.wait_for(
                update_job_weights(
                    second,
                    current_user=second_actor,
                    job_id=harness.jobs["parsed"],
                    payload=JobWeightsRequest.model_validate(
                        {
                            "w_skill": 0.6,
                            "w_semantic": 0.2,
                            "w_experience": 0.2,
                            "recalculate": True,
                        }
                    ),
                    dispatcher=second_dispatcher,
                ),
                timeout=5,
            )
            assert second_result.revision == 9
            first_dispatcher.release.set()
            first_result = await asyncio.wait_for(task, timeout=5)
        finally:
            first_dispatcher.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert first_result.revision == 8
    assert all(call[3] == 8 for call in first_dispatcher.calls)
    assert all(call[3] == 9 for call in second_dispatcher.calls)


@pytest.mark.asyncio
async def test_concurrent_weights_updates_serialize_on_current_job_state(
    weights_harness: Harness,
) -> None:
    harness = weights_harness
    entered = asyncio.Event()
    release = asyncio.Event()
    bind = harness.factory.kw["bind"]
    first = CommitGateSession(
        bind=bind,
        expire_on_commit=False,
        entered=entered,
        release=release,
    )
    second = harness.factory()
    observer = harness.factory()
    first_task: asyncio.Task[JobData] | None = None
    second_task: asyncio.Task[JobData] | None = None
    try:
        first_actor = await first.get(User, harness.users["hr"])
        second_actor = await second.get(User, harness.users["hr"])
        assert first_actor is not None and second_actor is not None
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        assert (
            isinstance(first_pid, int) and isinstance(second_pid, int) and first_pid != second_pid
        )
        first_task = asyncio.create_task(
            update_job_weights(
                first,
                current_user=first_actor,
                job_id=harness.jobs["parsed"],
                payload=JobWeightsRequest.model_validate(payload()),
                dispatcher=RecordingDispatcher(),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        second_task = asyncio.create_task(
            update_job_weights(
                second,
                current_user=second_actor,
                job_id=harness.jobs["parsed"],
                payload=JobWeightsRequest.model_validate(
                    {"w_skill": 0.6, "w_semantic": 0.2, "w_experience": 0.2}
                ),
                dispatcher=RecordingDispatcher(),
            )
        )
        await wait_for_blocker(observer, waiter_pid=second_pid, blocker_pid=first_pid)
        release.set()
        first_result, second_result = await asyncio.wait_for(
            asyncio.gather(first_task, second_task),
            timeout=10,
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

    assert (first_result.revision, second_result.revision) == (8, 9)
    async with harness.factory() as verify:
        job = await verify.get(JobDescription, harness.jobs["parsed"])
        rows = (
            await verify.scalars(
                select(MatchResult).where(MatchResult.job_id == harness.jobs["parsed"])
            )
        ).all()
        assert job is not None
        assert (job.revision, job.w_skill, job.w_semantic, job.w_experience) == (
            9,
            Decimal("0.600"),
            Decimal("0.200"),
            Decimal("0.200"),
        )
        assert {row.generation for row in rows} == {12, 13}
        assert all(row.job_revision == 9 and row.status == "PENDING" for row in rows)


@pytest.mark.asyncio
async def test_locking_reads_refresh_preloaded_stale_job_resume_and_match_state(
    weights_harness: Harness,
) -> None:
    harness = weights_harness
    async with harness.factory() as editor, harness.factory() as external:
        actor = await editor.get(User, harness.users["hr"])
        cached_job = await editor.get(JobDescription, harness.jobs["parsed"])
        cached_resumes = [await editor.get(Resume, row_id) for row_id in harness.resumes]
        cached_matches = [await editor.get(MatchResult, row_id) for row_id in harness.matches]
        assert actor is not None and cached_job is not None
        assert all(row is not None for row in cached_resumes + cached_matches)

        await external.execute(
            update(JobDescription)
            .where(JobDescription.id == harness.jobs["parsed"])
            .values(revision=20)
        )
        for index, resume_id in enumerate(harness.resumes):
            await external.execute(
                update(Resume).where(Resume.id == resume_id).values(revision=30 + index)
            )
        for index, match_id in enumerate(harness.matches):
            await external.execute(
                update(MatchResult).where(MatchResult.id == match_id).values(generation=40 + index)
            )
        await external.commit()

        result = await update_job_weights(
            editor,
            current_user=actor,
            job_id=harness.jobs["parsed"],
            payload=JobWeightsRequest.model_validate(payload()),
            dispatcher=RecordingDispatcher(),
        )

    assert result.revision == 21
    async with harness.factory() as verify:
        rows = (
            await verify.scalars(
                select(MatchResult).where(MatchResult.job_id == harness.jobs["parsed"])
            )
        ).all()
        assert {row.generation for row in rows} == {41, 42}
        assert {row.resume_revision for row in rows} == {30, 31}
        assert all(row.job_revision == 21 for row in rows)


@pytest.mark.asyncio
async def test_current_locked_parse_state_overrides_preloaded_parsed_identity(
    weights_harness: Harness,
) -> None:
    harness = weights_harness
    async with harness.factory() as editor, harness.factory() as external:
        actor = await editor.get(User, harness.users["hr"])
        cached_job = await editor.get(JobDescription, harness.jobs["parsed"])
        assert actor is not None and cached_job is not None
        assert cached_job.parsing_status == "PARSED"

        await external.execute(
            update(JobDescription)
            .where(JobDescription.id == harness.jobs["parsed"])
            .values(
                parsing_status="FAILED",
                parsing_error_message="controlled current failure",
                is_criteria_verified=False,
                job_embedding=None,
                embedding_model=None,
                embedding_preprocessing_version=None,
                parsed_at=None,
                status="DRAFT",
            )
        )
        await external.commit()

        with pytest.raises(APIError) as captured:
            await update_job_weights(
                editor,
                current_user=actor,
                job_id=harness.jobs["parsed"],
                payload=JobWeightsRequest.model_validate(payload(recalculate=True)),
                dispatcher=RecordingDispatcher(),
            )

    assert captured.value.status_code == 422
    assert captured.value.code == "JOB_NOT_READY"
    async with harness.factory() as verify:
        job = await verify.get(JobDescription, harness.jobs["parsed"])
        rows = (
            await verify.scalars(
                select(MatchResult).where(MatchResult.job_id == harness.jobs["parsed"])
            )
        ).all()
        assert job is not None and job.revision == 7
        assert {row.generation for row in rows} == {10, 11}


@pytest.mark.asyncio
async def test_no_autoflush_guard_orders_real_constraint_failure_after_all_locks(
    weights_harness: Harness,
) -> None:
    harness = weights_harness
    observations: list[str] = []
    async with harness.factory() as session:
        actor = await session.get(User, harness.users["hr"])
        assert actor is not None
        duplicate = make_user(uuid.uuid4().hex, "HR", "duplicate")
        duplicate.email = actor.email
        session.add(duplicate)

        def observe_flush(*_: Any) -> None:
            observations.append("flush")

        def observe_statement(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            if "FOR UPDATE" not in statement:
                return
            if "job_descriptions" in statement:
                observations.append("job_lock")
            elif "resumes" in statement:
                observations.append("resume_lock")
            elif "match_results" in statement:
                observations.append("match_lock")

        assert session.bind is not None
        sync_engine = session.bind.sync_engine
        event.listen(session.sync_session, "before_flush", observe_flush)
        event.listen(sync_engine, "before_cursor_execute", observe_statement)
        try:
            with pytest.raises(SQLAlchemyError):
                await update_job_weights(
                    session,
                    current_user=actor,
                    job_id=harness.jobs["parsed"],
                    payload=JobWeightsRequest.model_validate(payload()),
                    dispatcher=RecordingDispatcher(),
                )
        finally:
            event.remove(session.sync_session, "before_flush", observe_flush)
            event.remove(sync_engine, "before_cursor_execute", observe_statement)
            await session.rollback()

    assert observations == ["job_lock", "resume_lock", "match_lock", "flush"]


@pytest.mark.asyncio
async def test_no_autoflush_negative_control_fails_before_job_lock(
    weights_harness: Harness,
) -> None:
    harness = weights_harness
    observations: list[str] = []
    async with harness.factory() as session:
        actor = await session.get(User, harness.users["hr"])
        assert actor is not None
        duplicate = make_user(uuid.uuid4().hex, "HR", "negative-control")
        duplicate.email = actor.email
        session.add(duplicate)

        def observe_flush(*_: Any) -> None:
            observations.append("flush")

        def observe_statement(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            if "FOR UPDATE" in statement and "job_descriptions" in statement:
                observations.append("job_lock")

        assert session.bind is not None
        sync_engine = session.bind.sync_engine
        event.listen(session.sync_session, "before_flush", observe_flush)
        event.listen(sync_engine, "before_cursor_execute", observe_statement)
        try:
            with pytest.raises(SQLAlchemyError):
                await update_job_weights(
                    AutoflushEnabledSession(session),  # type: ignore[arg-type]
                    current_user=actor,
                    job_id=harness.jobs["parsed"],
                    payload=JobWeightsRequest.model_validate(payload()),
                    dispatcher=RecordingDispatcher(),
                )
        finally:
            event.remove(session.sync_session, "before_flush", observe_flush)
            event.remove(sync_engine, "before_cursor_execute", observe_statement)
            await session.rollback()

    assert observations == ["flush"]
