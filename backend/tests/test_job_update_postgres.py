from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.engine_factory import create_engine
from app.core.exceptions import APIError
from app.core.security import create_access_token
from app.core.version_guard import claim_match_generation
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import CandidateProfile, Resume
from app.models.skill import JobSkill, Skill
from app.models.user import User
from app.schemas.job_schema import JobCriteriaRequest, JobUpdateRequest
from app.schemas.match_schema import MatchCalculateRequest
from app.schemas.resume_schema import ResumeParsedDataUpdate
from app.services.job_dispatcher import get_job_parse_dispatcher
from app.services.job_recovery import recover_pending_jobs, select_job_recovery_candidates
from app.services.job_service import soft_delete_job, update_job, update_job_criteria
from app.services.match_service import calculate_matches
from app.services.resume_service import update_resume_parsed_data
from app.workers.job_parse_worker import JobParseTaskOutcome, process_job_parse_task
from app.workers.match_worker import MatchTaskOutcome, _terminal_update, process_match_task
from main import app

TEST_SECRET = "job-update-postgres-jwt-secret-value"


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, int]] = []
        self.fail = False
        self.pause = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def dispatch(self, job_id: uuid.UUID, revision: int) -> None:
        self.calls.append((job_id, revision))
        if self.pause:
            self.entered.set()
            await self.release.wait()
        if self.fail:
            raise RuntimeError("private broker URL")


class GatedRecoveryDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, int]] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def dispatch(self, job_id: uuid.UUID, revision: int) -> None:
        self.calls.append((job_id, revision))
        self.entered.set()
        await self.release.wait()


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


class ControlledEmbeddingProvider:
    model_name = BGE_M3_MODEL_NAME
    dimension = BGE_M3_EMBEDDING_DIMENSION

    async def embed(self, text_value: str) -> list[float]:
        assert text_value
        return [0.125] * self.dimension


class FailingEmbeddingProvider(ControlledEmbeddingProvider):
    async def embed(self, text_value: str) -> list[float]:
        del text_value
        raise RuntimeError("private embedding path")


class GatedEmbeddingProvider(ControlledEmbeddingProvider):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def embed(self, text_value: str) -> list[float]:
        self.entered.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError("private embedding path")
        return await super().embed(text_value)


class CommitGateSession(AsyncSession):
    def __init__(
        self,
        *args: Any,
        commit_entered: asyncio.Event,
        commit_release: asyncio.Event,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.commit_entered = commit_entered
        self.commit_release = commit_release

    async def commit(self) -> None:
        self.commit_entered.set()
        await self.commit_release.wait()
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


class ExistingSessionFactory:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.entered = asyncio.Event()
        self.pid: int | None = None

    @asynccontextmanager
    async def context(self) -> AsyncIterator[AsyncSession]:
        self.pid = await self.session.scalar(text("SELECT pg_backend_pid()"))
        assert self.pid is not None
        self.entered.set()
        yield self.session

    def __call__(self) -> Any:
        return self.context()


@dataclass(frozen=True)
class JobUpdateHarness:
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


def make_user(unique: str, name: str, role: str, *, active: bool = True) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=f"job.update.{unique}.{name}@example.com",
        password_hash="not-used",
        full_name=f"Job Update {name}",
        role=role,
        is_active=active,
        created_at=now,
        updated_at=now,
    )


def make_job(
    owner_id: uuid.UUID,
    name: str,
    parsing_status: str,
    *,
    deleted: bool = False,
) -> JobDescription:
    now = datetime.now(UTC)
    parsed = parsing_status == "PARSED"
    failed = parsing_status == "FAILED"
    return JobDescription(
        id=uuid.uuid4(),
        recruiter_id=owner_id,
        title=f"Original {name}",
        job_level="SENIOR",
        location="Hanoi",
        raw_content=f"Original raw content {name}",
        create_request_fingerprint=hashlib.sha256(f"create-{name}".encode()).hexdigest(),
        revision=7,
        min_experience_years=Decimal("4.0"),
        education_requirement="Bachelor",
        job_embedding=[0.2] * 1024 if parsed else None,
        embedding_model="BAAI/bge-m3" if parsed else None,
        embedding_preprocessing_version="resume-text-v1" if parsed else None,
        parsing_status=parsing_status,
        parsing_error_message="old parse error" if failed else None,
        is_criteria_verified=parsed,
        w_skill=Decimal("0.500"),
        w_semantic=Decimal("0.300"),
        w_experience=Decimal("0.200"),
        status="ACTIVE" if parsed else "CLOSED",
        is_deleted=deleted,
        created_at=now,
        updated_at=now,
        parsed_at=now if parsed else None,
        deleted_at=now if deleted else None,
    )


def make_resume(owner_id: uuid.UUID, index: int, *, deleted: bool) -> Resume:
    now = datetime.now(UTC)
    return Resume(
        id=uuid.uuid4(),
        owner_user_id=owner_id,
        file_name=f"resume-{index}.pdf",
        storage_key=f"job-update/{uuid.uuid4()}/source",
        file_size=128,
        mime_type="application/pdf",
        create_request_fingerprint=hashlib.sha256(f"resume-{index}".encode()).hexdigest(),
        revision=index + 2,
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


def make_match(job: JobDescription, resume: Resume, index: int, status: str) -> MatchResult:
    now = datetime.now(UTC)
    completed = status == "COMPLETED"
    failed = status == "FAILED"
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
        status=status,
        error_message="old match error" if failed else None,
        created_at=now,
        updated_at=now,
        calculated_at=now if completed else None,
    )


@pytest_asyncio.fixture
async def job_update_postgres() -> AsyncIterator[JobUpdateHarness]:
    configured = Settings()
    if configured.database_url is None:
        pytest.skip("DATABASE_URL is not configured")
    database_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=configured.database_url,
        database_connect_timeout_seconds=configured.database_connect_timeout_seconds,
    )
    settings = Settings(_env_file=None, app_env="test", jwt_secret_key=TEST_SECRET)
    engine = create_engine(database_settings)
    assert engine is not None
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 - never expose database credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    unique = uuid.uuid4().hex
    users = {
        "hr": make_user(unique, "hr", "HR"),
        "other_hr": make_user(unique, "other-hr", "HR"),
        "admin": make_user(unique, "admin", "ADMIN"),
        "candidate": make_user(unique, "candidate", "CANDIDATE"),
        "inactive": make_user(unique, "inactive", "HR", active=False),
    }
    jobs = {
        state.lower(): make_job(users["hr"].id, state.lower(), state)
        for state in ("PENDING", "PROCESSING", "PARSED", "FAILED")
    }
    jobs["other"] = make_job(users["other_hr"].id, "other", "PARSED")
    jobs["deleted"] = make_job(users["hr"].id, "deleted", "PARSED", deleted=True)
    resumes = tuple(make_resume(users["hr"].id, index, deleted=index == 4) for index in range(5))
    match_statuses = ("PENDING", "PROCESSING", "COMPLETED", "FAILED", "COMPLETED")
    matches = tuple(
        make_match(jobs["parsed"], resume, index, match_statuses[index])
        for index, resume in enumerate(resumes)
    )
    async with factory() as setup:
        skill_id = await setup.scalar(select(Skill.id).order_by(Skill.id.asc()).limit(1))
        if skill_id is None:
            await engine.dispose()
            pytest.fail("Configured PostgreSQL database has no taxonomy seed", pytrace=False)
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
                min_years_required=Decimal("3.0"),
            )
        )
        setup.add_all(matches)
        await setup.commit()

    dispatcher = RecordingDispatcher()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_job_parse_dispatcher] = lambda: dispatcher
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield JobUpdateHarness(
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


def freeze(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, freeze(item)) for key, item in value.items()))
    return value


def snapshot(row: Any) -> tuple[Any, ...]:
    return tuple(freeze(getattr(row, column.name)) for column in row.__table__.columns)


@pytest.mark.asyncio
async def test_real_http_auth_ownership_missing_deleted_and_invalid_uuid(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    target = harness.jobs["parsed"]
    cases = (
        ({}, 401, "AUTHENTICATION_REQUIRED"),
        ({"Authorization": "Bearer invalid"}, 401, "INVALID_ACCESS_TOKEN"),
        (harness.headers("inactive"), 403, "ACCOUNT_INACTIVE"),
        (harness.headers("candidate"), 403, "INSUFFICIENT_PERMISSIONS"),
        (harness.headers("other_hr"), 404, "JOB_NOT_FOUND"),
    )
    for headers, status_code, code in cases:
        response = await harness.client.put(
            f"/api/v1/jobs/{target}", headers=headers, json={"title": "Denied"}
        )
        assert response.status_code == status_code
        assert response.json()["error"]["code"] == code

    for job_id in (harness.jobs["deleted"], uuid.uuid4()):
        response = await harness.client.put(
            f"/api/v1/jobs/{job_id}",
            headers=harness.headers("hr"),
            json={"title": "Unavailable"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "JOB_NOT_FOUND"

    invalid = await harness.client.put(
        "/api/v1/jobs/not-a-uuid",
        headers=harness.headers("hr"),
        json={"title": "Invalid"},
    )
    assert invalid.status_code == 422

    admin = await harness.client.put(
        f"/api/v1/jobs/{harness.jobs['other']}",
        headers=harness.headers("admin"),
        json={"title": "Admin updated"},
    )
    assert admin.status_code == 200
    assert admin.json()["data"]["title"] == "Admin updated"


@pytest.mark.asyncio
async def test_metadata_only_and_noop_preserve_computation_state_in_all_parse_states(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    for state in ("pending", "processing", "parsed", "failed"):
        job_id = harness.jobs[state]
        async with harness.factory() as session:
            before_row = await session.get(JobDescription, job_id)
            assert before_row is not None
            before = snapshot(before_row)
            before_updated = before_row.updated_at
        response = await harness.client.put(
            f"/api/v1/jobs/{job_id}",
            headers=harness.headers("hr"),
            json={"location": f"  District {state}  ", "raw_content": before_row.raw_content},
        )
        assert response.status_code == 200
        async with harness.factory() as session:
            after_row = await session.get(JobDescription, job_id)
            assert after_row is not None
            assert after_row.location == f"District {state}"
            assert after_row.updated_at >= before_updated
            before_values = list(before)
            after_values = list(snapshot(after_row))
            for field in ("location", "updated_at"):
                index = list(JobDescription.__table__.columns.keys()).index(field)
                before_values[index] = after_values[index]
            assert tuple(after_values) == tuple(before_values)

        no_op_before = snapshot(after_row)
        no_op = await harness.client.put(
            f"/api/v1/jobs/{job_id}",
            headers=harness.headers("hr"),
            json={"location": f"District {state}"},
        )
        assert no_op.status_code == 200
        async with harness.factory() as session:
            no_op_after = await session.get(JobDescription, job_id)
            assert no_op_after is not None
            assert snapshot(no_op_after) == no_op_before
    assert harness.dispatcher.calls == []


@pytest.mark.asyncio
async def test_raw_change_atomically_resets_job_and_every_match_then_dispatches_exact_revision(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    async with harness.factory() as session:
        before_job = await session.get(JobDescription, job_id)
        assert before_job is not None
        fingerprint = before_job.create_request_fingerprint
        weights = (before_job.w_skill, before_job.w_semantic, before_job.w_experience)
        created_at = before_job.created_at
        resume_snapshots = {
            resume_id: snapshot(await session.get(Resume, resume_id))
            for resume_id in harness.resumes
        }
        generations = {
            match_id: (await session.get(MatchResult, match_id)).generation
            for match_id in harness.matches
        }
        old_match = await session.get(MatchResult, harness.matches[0])
        assert old_match is not None
        old_match_payload = (
            old_match.id,
            old_match.generation,
            old_match.resume_revision,
            old_match.job_revision,
            old_match.algorithm_version,
        )
        linked_resume = await session.get(Resume, old_match.resume_id)
        assert linked_resume is not None
        assert old_match.status == "PENDING"
        assert old_match.resume_revision == linked_resume.revision
        assert old_match.job_revision == before_job.revision

    response = await harness.client.put(
        f"/api/v1/jobs/{job_id}",
        headers=harness.headers("hr"),
        json={
            "title": "  Reparsed title  ",
            "location": None,
            "raw_content": "New raw\r\ncontent",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["revision"] == 8
    assert harness.dispatcher.calls == [(job_id, 8)]

    async with harness.factory() as session:
        job = await session.get(JobDescription, job_id)
        assert job is not None
        assert (job.title, job.location, job.raw_content) == (
            "Reparsed title",
            None,
            "New raw\ncontent",
        )
        assert (job.revision, job.status, job.parsing_status, job.is_criteria_verified) == (
            8,
            "DRAFT",
            "PENDING",
            False,
        )
        assert job.job_embedding is None and job.embedding_model is None
        assert job.embedding_preprocessing_version is None and job.parsed_at is None
        assert job.parsing_error_message is None
        assert job.create_request_fingerprint == fingerprint
        assert (job.w_skill, job.w_semantic, job.w_experience) == weights
        assert job.created_at == created_at
        skills = tuple(
            (await session.scalars(select(JobSkill).where(JobSkill.job_id == job_id))).all()
        )
        assert len(skills) == 1 and skills[0].skill_id == harness.skill_id
        for resume_id in harness.resumes:
            row = await session.get(Resume, resume_id)
            assert row is not None and snapshot(row) == resume_snapshots[resume_id]
        for match_id in harness.matches:
            match = await session.get(MatchResult, match_id)
            assert match is not None
            resume = await session.get(Resume, match.resume_id)
            assert resume is not None
            assert match.generation == generations[match_id] + 1
            assert (match.resume_revision, match.job_revision, match.status) == (
                resume.revision,
                8,
                "PENDING",
            )
            assert match.overall_score is None and match.skill_score is None
            assert match.semantic_score is None and match.experience_score is None
            assert match.matched_skills == [] and match.missing_skills == []
            assert match.gap_analysis_summary is None and match.error_message is None
            assert match.embedding_model is None
            assert match.embedding_preprocessing_version is None
            assert match.calculated_at is None

    stale_match_outcome = await process_match_task(
        *old_match_payload,
        session_factory=harness.factory,
    )
    assert stale_match_outcome is MatchTaskOutcome.DISCARDED


@pytest.mark.asyncio
async def test_raw_updates_all_parse_states_no_matches_and_dispatch_failure_is_best_effort(
    job_update_postgres: JobUpdateHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = job_update_postgres
    harness.dispatcher.fail = True
    for state in ("pending", "processing", "failed"):
        job_id = harness.jobs[state]
        if state == "pending":
            async with harness.factory() as session:
                eligible = await session.get(JobDescription, job_id)
                assert eligible is not None
                assert (eligible.revision, eligible.parsing_status, eligible.is_deleted) == (
                    7,
                    "PENDING",
                    False,
                )
        response = await harness.client.put(
            f"/api/v1/jobs/{job_id}",
            headers=harness.headers("hr"),
            json={"raw_content": f"new {state} source"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["revision"] == 8
        async with harness.factory() as session:
            row = await session.get(JobDescription, job_id)
            assert row is not None
            assert (row.revision, row.status, row.parsing_status) == (8, "DRAFT", "PENDING")
            assert row.parsing_error_message is None
        if state == "pending":
            stale_job_outcome = await process_job_parse_task(
                job_id,
                7,
                session_factory=harness.factory,
                embedding_provider=ControlledEmbeddingProvider(),
            )
            assert stale_job_outcome is JobParseTaskOutcome.DISCARDED
    assert harness.dispatcher.calls == [
        (harness.jobs["pending"], 8),
        (harness.jobs["processing"], 8),
        (harness.jobs["failed"], 8),
    ]
    assert "error_type=RuntimeError" in caplog.text
    assert "private broker URL" not in caplog.text


@pytest.mark.asyncio
async def test_raw_update_flows_through_worker_review_activation_and_matching_trigger(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    updated = await harness.client.put(
        f"/api/v1/jobs/{job_id}",
        headers=harness.headers("hr"),
        json={"raw_content": "Requirements\nPython required\n4 years experience"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["revision"] == 8

    outcome = await process_job_parse_task(
        job_id,
        8,
        session_factory=harness.factory,
        embedding_provider=ControlledEmbeddingProvider(),
    )
    assert outcome is JobParseTaskOutcome.PARSED
    async with harness.factory() as session:
        parsed = await session.get(JobDescription, job_id)
        assert parsed is not None
        parsed_skill_id = await session.scalar(
            select(JobSkill.skill_id).where(JobSkill.job_id == job_id)
        )
        assert parsed_skill_id is not None
        assert (parsed.revision, parsed.parsing_status, parsed.is_criteria_verified) == (
            8,
            "PARSED",
            False,
        )

    reviewed = await harness.client.put(
        f"/api/v1/jobs/{job_id}/criteria",
        headers=harness.headers("hr"),
        json={
            "min_experience_years": 4,
            "education_requirement": None,
            "skills": [
                {
                    "skill_id": parsed_skill_id,
                    "importance": "MANDATORY",
                    "min_years_required": 4,
                }
            ],
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["data"]["revision"] == 9

    activated = await harness.client.patch(
        f"/api/v1/jobs/{job_id}/status",
        headers=harness.headers("hr"),
        json={"status": "ACTIVE"},
    )
    assert activated.status_code == 200
    assert activated.json()["data"]["revision"] == 9

    triggered = await harness.client.post(
        "/api/v1/matching/calculate",
        headers=harness.headers("hr"),
        json={"job_id": str(job_id), "resume_ids": [str(harness.resumes[0])]},
    )
    assert triggered.status_code == 202
    assert triggered.json()["data"]["total_matches"] == 1
    async with harness.factory() as session:
        prepared = await session.scalar(
            select(MatchResult).where(
                MatchResult.job_id == job_id,
                MatchResult.resume_id == harness.resumes[0],
            )
        )
        assert prepared is not None
        assert (prepared.job_revision, prepared.status) == (9, "PENDING")


async def load_actor(session: AsyncSession, actor_id: uuid.UUID) -> User:
    actor = await session.get(User, actor_id)
    assert actor is not None
    return actor


async def wait_for_blocker(
    observer: AsyncSession,
    *,
    waiter_pid: int,
    blocker_pid: int,
) -> tuple[int, ...]:
    async with asyncio.timeout(5):
        while True:
            blockers = await observer.scalar(
                text("SELECT pg_blocking_pids(:waiter_pid)"),
                {"waiter_pid": waiter_pid},
            )
            if blocker_pid in blockers:
                return tuple(blockers)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_raw", [False, True], ids=["different-raw", "same-raw"])
async def test_concurrent_raw_updates_wait_on_actual_job_lock_and_serialize_current_state(
    job_update_postgres: JobUpdateHarness,
    same_raw: bool,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    entered = asyncio.Event()
    release = asyncio.Event()
    first_dispatcher = RecordingDispatcher()
    second_dispatcher = RecordingDispatcher()
    bind = harness.factory.kw["bind"]
    first = CommitGateSession(
        bind=bind,
        expire_on_commit=False,
        commit_entered=entered,
        commit_release=release,
    )
    second = harness.factory()
    observer = harness.factory()
    first_task: asyncio.Task[JobDescription] | None = None
    second_task: asyncio.Task[JobDescription] | None = None
    try:
        first_actor = await load_actor(first, harness.users["hr"])
        second_actor = await load_actor(second, harness.users["hr"])
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(first_pid, int) and isinstance(second_pid, int)
        assert first_pid != second_pid
        initial_generations = {
            row.id: row.generation
            for row in (
                await observer.scalars(select(MatchResult).where(MatchResult.job_id == job_id))
            ).all()
        }

        first_task = asyncio.create_task(
            update_job(
                first,
                current_user=first_actor,
                job_id=job_id,
                payload=JobUpdateRequest(raw_content="contended first raw"),
                dispatcher=first_dispatcher,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        second_raw = "contended first raw" if same_raw else "contended second raw"
        second_task = asyncio.create_task(
            update_job(
                second,
                current_user=second_actor,
                job_id=job_id,
                payload=JobUpdateRequest(raw_content=second_raw),
                dispatcher=second_dispatcher,
            )
        )
        assert await wait_for_blocker(
            observer,
            waiter_pid=second_pid,
            blocker_pid=first_pid,
        ) == (first_pid,)
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

    expected_revision = 8 if same_raw else 9
    assert (first_result.revision, second_result.revision) == (8, expected_revision)
    assert first_dispatcher.calls == [(job_id, 8)]
    assert second_dispatcher.calls == ([] if same_raw else [(job_id, 9)])
    async with harness.factory() as verify:
        job = await verify.get(JobDescription, job_id)
        assert job is not None
        assert (job.revision, job.raw_content) == (expected_revision, second_raw)
        matches = (
            await verify.scalars(select(MatchResult).where(MatchResult.job_id == job_id))
        ).all()
        increment = 1 if same_raw else 2
        assert all(
            row.generation == initial_generations[row.id] + increment
            and row.job_revision == expected_revision
            for row in matches
        )


@pytest.mark.asyncio
async def test_contended_partial_metadata_updates_preserve_preceding_omitted_field(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    entered = asyncio.Event()
    release = asyncio.Event()
    bind = harness.factory.kw["bind"]
    first = CommitGateSession(
        bind=bind,
        expire_on_commit=False,
        commit_entered=entered,
        commit_release=release,
    )
    second = harness.factory()
    observer = harness.factory()
    first_task: asyncio.Task[JobDescription] | None = None
    second_task: asyncio.Task[JobDescription] | None = None
    try:
        first_actor = await load_actor(first, harness.users["hr"])
        second_actor = await load_actor(second, harness.users["hr"])
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(first_pid, int) and isinstance(second_pid, int)
        first_task = asyncio.create_task(
            update_job(
                first,
                current_user=first_actor,
                job_id=job_id,
                payload=JobUpdateRequest(title="first title"),
                dispatcher=RecordingDispatcher(),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        second_task = asyncio.create_task(
            update_job(
                second,
                current_user=second_actor,
                job_id=job_id,
                payload=JobUpdateRequest(job_level="second level"),
                dispatcher=RecordingDispatcher(),
            )
        )
        assert await wait_for_blocker(
            observer,
            waiter_pid=second_pid,
            blocker_pid=first_pid,
        ) == (first_pid,)
        release.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=10)
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
        job = await verify.get(JobDescription, job_id)
        assert job is not None
        assert (job.title, job.job_level, job.revision) == ("first title", "second level", 7)


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_first", [True, False], ids=["delete-first", "update-first"])
async def test_soft_delete_and_raw_update_serialize_both_job_lock_orders(
    job_update_postgres: JobUpdateHarness,
    delete_first: bool,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
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
    dispatcher = RecordingDispatcher()
    winner_task: asyncio.Task[Any] | None = None
    waiter_task: asyncio.Task[Any] | None = None
    try:
        winner_actor = await load_actor(winner, harness.users["hr"])
        waiter_actor = await load_actor(waiter, harness.users["hr"])
        winner_pid = await winner.scalar(text("SELECT pg_backend_pid()"))
        waiter_pid = await waiter.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(winner_pid, int) and isinstance(waiter_pid, int)
        if delete_first:
            winner_task = asyncio.create_task(
                soft_delete_job(winner, current_user=winner_actor, job_id=job_id)
            )
        else:
            winner_task = asyncio.create_task(
                update_job(
                    winner,
                    current_user=winner_actor,
                    job_id=job_id,
                    payload=JobUpdateRequest(raw_content="update before delete"),
                    dispatcher=dispatcher,
                )
            )
        await asyncio.wait_for(entered.wait(), timeout=5)
        if delete_first:
            waiter_task = asyncio.create_task(
                update_job(
                    waiter,
                    current_user=waiter_actor,
                    job_id=job_id,
                    payload=JobUpdateRequest(raw_content="must not resurrect"),
                    dispatcher=dispatcher,
                )
            )
        else:
            waiter_task = asyncio.create_task(
                soft_delete_job(waiter, current_user=waiter_actor, job_id=job_id)
            )
        assert await wait_for_blocker(
            observer,
            waiter_pid=waiter_pid,
            blocker_pid=winner_pid,
        ) == (winner_pid,)
        release.set()
        if delete_first:
            await asyncio.wait_for(winner_task, timeout=5)
            with pytest.raises(APIError) as rejected:
                await asyncio.wait_for(waiter_task, timeout=5)
            assert rejected.value.status_code == 404
        else:
            await asyncio.wait_for(asyncio.gather(winner_task, waiter_task), timeout=10)
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
        assert job is not None and job.is_deleted is True
        matches = (
            await verify.scalars(select(MatchResult).where(MatchResult.job_id == job_id))
        ).all()
        if delete_first:
            assert job.revision == 7
            assert dispatcher.calls == []
            assert all(match.job_revision == 7 for match in matches)
        else:
            assert job.revision == 8
            assert dispatcher.calls == [(job_id, 8)]
            assert all(match.job_revision == 8 for match in matches)
            outcome = await process_job_parse_task(
                job_id,
                8,
                session_factory=harness.factory,
                embedding_provider=ControlledEmbeddingProvider(),
            )
            assert outcome is JobParseTaskOutcome.DISCARDED


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["success", "failed"])
async def test_claimed_old_parse_worker_cannot_terminal_write_after_raw_update(
    job_update_postgres: JobUpdateHarness,
    terminal: str,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["pending"]
    provider = GatedEmbeddingProvider(fail=terminal == "failed")
    worker = asyncio.create_task(
        process_job_parse_task(
            job_id,
            7,
            session_factory=harness.factory,
            embedding_provider=provider,
        )
    )
    try:
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        async with harness.factory() as claimed_session:
            claimed = await claimed_session.get(JobDescription, job_id)
            assert claimed is not None
            assert (claimed.revision, claimed.parsing_status) == (7, "PROCESSING")
        updated = await harness.client.put(
            f"/api/v1/jobs/{job_id}",
            headers=harness.headers("hr"),
            json={"raw_content": f"new source while old {terminal} is running"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["revision"] == 8
        provider.release.set()
        outcome = await asyncio.wait_for(worker, timeout=10)
    finally:
        provider.release.set()
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    assert outcome is JobParseTaskOutcome.DISCARDED
    async with harness.factory() as verify:
        job = await verify.get(JobDescription, job_id)
        assert job is not None
        assert (job.revision, job.parsing_status, job.parsing_error_message) == (
            8,
            "PENDING",
            None,
        )
        assert job.job_embedding is None and job.is_criteria_verified is False


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["success", "failed"])
async def test_parse_terminal_commit_then_raw_update_prepares_exact_next_revision(
    job_update_postgres: JobUpdateHarness,
    terminal: str,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["pending"]
    provider = (
        ControlledEmbeddingProvider() if terminal == "success" else FailingEmbeddingProvider()
    )
    outcome = await process_job_parse_task(
        job_id,
        7,
        session_factory=harness.factory,
        embedding_provider=provider,
    )
    assert outcome is (
        JobParseTaskOutcome.PARSED if terminal == "success" else JobParseTaskOutcome.FAILED
    )
    async with harness.factory() as terminal_session:
        terminal_job = await terminal_session.get(JobDescription, job_id)
        assert terminal_job is not None
        assert terminal_job.revision == 7
        assert terminal_job.parsing_status == ("PARSED" if terminal == "success" else "FAILED")

    updated = await harness.client.put(
        f"/api/v1/jobs/{job_id}",
        headers=harness.headers("hr"),
        json={"raw_content": f"source after terminal {terminal}"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["revision"] == 8
    async with harness.factory() as verify:
        job = await verify.get(JobDescription, job_id)
        assert job is not None
        assert (job.revision, job.status, job.parsing_status) == (8, "DRAFT", "PENDING")
        assert job.parsing_error_message is None and job.job_embedding is None


@pytest.mark.asyncio
async def test_metadata_update_during_parse_is_preserved_without_new_computation(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["pending"]
    provider = GatedEmbeddingProvider()
    worker = asyncio.create_task(
        process_job_parse_task(
            job_id,
            7,
            session_factory=harness.factory,
            embedding_provider=provider,
        )
    )
    try:
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        response = await harness.client.put(
            f"/api/v1/jobs/{job_id}",
            headers=harness.headers("hr"),
            json={"title": "metadata while parsing"},
        )
        assert response.status_code == 200
        provider.release.set()
        assert await asyncio.wait_for(worker, timeout=10) is JobParseTaskOutcome.PARSED
    finally:
        provider.release.set()
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    async with harness.factory() as verify:
        job = await verify.get(JobDescription, job_id)
        assert job is not None
        assert (job.title, job.revision, job.parsing_status) == (
            "metadata while parsing",
            7,
            "PARSED",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("criteria_first", [True, False], ids=["criteria-first", "update-first"])
async def test_criteria_and_raw_update_serialize_both_orders_from_current_job_state(
    job_update_postgres: JobUpdateHarness,
    criteria_first: bool,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
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
    dispatcher = RecordingDispatcher()
    criteria = JobCriteriaRequest.model_validate(
        {
            "min_experience_years": 5,
            "education_requirement": "Reviewed",
            "skills": [
                {
                    "skill_id": harness.skill_id,
                    "importance": "MANDATORY",
                    "min_years_required": 3,
                }
            ],
        }
    )
    winner_task: asyncio.Task[Any] | None = None
    waiter_task: asyncio.Task[Any] | None = None
    try:
        winner_actor = await load_actor(winner, harness.users["hr"])
        waiter_actor = await load_actor(waiter, harness.users["hr"])
        winner_pid = await winner.scalar(text("SELECT pg_backend_pid()"))
        waiter_pid = await waiter.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(winner_pid, int) and isinstance(waiter_pid, int)
        if criteria_first:
            winner_task = asyncio.create_task(
                update_job_criteria(
                    winner,
                    current_user=winner_actor,
                    job_id=job_id,
                    payload=criteria,
                )
            )
        else:
            winner_task = asyncio.create_task(
                update_job(
                    winner,
                    current_user=winner_actor,
                    job_id=job_id,
                    payload=JobUpdateRequest(raw_content="raw wins before criteria"),
                    dispatcher=dispatcher,
                )
            )
        await asyncio.wait_for(entered.wait(), timeout=5)
        if criteria_first:
            waiter_task = asyncio.create_task(
                update_job(
                    waiter,
                    current_user=waiter_actor,
                    job_id=job_id,
                    payload=JobUpdateRequest(raw_content="raw after reviewed criteria"),
                    dispatcher=dispatcher,
                )
            )
        else:
            waiter_task = asyncio.create_task(
                update_job_criteria(
                    waiter,
                    current_user=waiter_actor,
                    job_id=job_id,
                    payload=criteria,
                )
            )
        assert await wait_for_blocker(
            observer,
            waiter_pid=waiter_pid,
            blocker_pid=winner_pid,
        ) == (winner_pid,)
        release.set()
        await asyncio.wait_for(winner_task, timeout=5)
        if criteria_first:
            await asyncio.wait_for(waiter_task, timeout=5)
        else:
            with pytest.raises(APIError) as not_ready:
                await asyncio.wait_for(waiter_task, timeout=5)
            assert not_ready.value.status_code == 422
            assert not_ready.value.code == "JOB_NOT_READY"
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
        assert job is not None
        matches = (
            await verify.scalars(select(MatchResult).where(MatchResult.job_id == job_id))
        ).all()
        if criteria_first:
            assert (job.revision, job.parsing_status, job.is_criteria_verified) == (
                9,
                "PENDING",
                False,
            )
            assert all(match.job_revision == 9 for match in matches)
        else:
            assert (job.revision, job.parsing_status, job.is_criteria_verified) == (
                8,
                "PENDING",
                False,
            )
            assert all(match.job_revision == 8 for match in matches)


@pytest.mark.asyncio
async def test_recovery_selection_before_raw_update_publishes_old_immutable_revision_and_goes_stale(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["pending"]
    old_time = datetime.now(UTC) - timedelta(minutes=10)
    async with harness.factory() as setup:
        await setup.execute(
            update(JobDescription).where(JobDescription.id == job_id).values(updated_at=old_time)
        )
        await setup.commit()

    recovery_dispatcher = GatedRecoveryDispatcher()
    recovery_task = asyncio.create_task(
        recover_pending_jobs(
            harness.factory,
            recovery_dispatcher,
            grace_seconds=60,
            batch_size=1,
            now=datetime.now(UTC),
        )
    )
    try:
        await asyncio.wait_for(recovery_dispatcher.entered.wait(), timeout=5)
        assert recovery_dispatcher.calls == [(job_id, 7)]
        updated = await asyncio.wait_for(
            harness.client.put(
                f"/api/v1/jobs/{job_id}",
                headers=harness.headers("hr"),
                json={"raw_content": "raw after recovery selection"},
            ),
            timeout=5,
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["revision"] == 8
        recovery_dispatcher.release.set()
        result = await asyncio.wait_for(recovery_task, timeout=5)
    finally:
        recovery_dispatcher.release.set()
        if not recovery_task.done():
            recovery_task.cancel()
        await asyncio.gather(recovery_task, return_exceptions=True)

    assert (result.selected, result.dispatched, result.failed) == (1, 1, 0)
    stale = await process_job_parse_task(
        job_id,
        7,
        session_factory=harness.factory,
        embedding_provider=ControlledEmbeddingProvider(),
    )
    assert stale is JobParseTaskOutcome.DISCARDED
    current = await select_job_recovery_candidates(
        harness.factory,
        cutoff=datetime.now(UTC) + timedelta(seconds=1),
        batch_size=100,
    )
    assert any(candidate.job_id == job_id and candidate.revision == 8 for candidate in current)
    async with harness.factory() as verify:
        job = await verify.get(JobDescription, job_id)
        assert job is not None
        assert (job.revision, job.parsing_status, job.raw_content) == (
            8,
            "PENDING",
            "raw after recovery selection",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_pair", [True, False], ids=["existing-pair", "new-pair"])
@pytest.mark.parametrize("trigger_first", [True, False], ids=["trigger-first", "update-first"])
async def test_matching_trigger_and_raw_update_serialize_all_pair_and_lock_order_variants(
    job_update_postgres: JobUpdateHarness,
    existing_pair: bool,
    trigger_first: bool,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    resume_id = harness.resumes[0]
    if not existing_pair:
        new_resume = make_resume(harness.users["hr"], 40, deleted=False)
        resume_id = new_resume.id
        async with harness.factory() as setup:
            setup.add(new_resume)
            await setup.commit()

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
    job_dispatcher = RecordingDispatcher()
    match_dispatcher = RecordingMatchDispatcher()
    request = MatchCalculateRequest(job_id=job_id, resume_ids=[resume_id])
    winner_task: asyncio.Task[Any] | None = None
    waiter_task: asyncio.Task[Any] | None = None
    try:
        winner_actor = await load_actor(winner, harness.users["hr"])
        waiter_actor = await load_actor(waiter, harness.users["hr"])
        winner_pid = await winner.scalar(text("SELECT pg_backend_pid()"))
        waiter_pid = await waiter.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(winner_pid, int) and isinstance(waiter_pid, int)
        if trigger_first:
            winner_task = asyncio.create_task(
                calculate_matches(
                    winner,
                    current_user=winner_actor,
                    payload=request,
                    dispatcher=match_dispatcher,
                )
            )
        else:
            winner_task = asyncio.create_task(
                update_job(
                    winner,
                    current_user=winner_actor,
                    job_id=job_id,
                    payload=JobUpdateRequest(raw_content="update before trigger"),
                    dispatcher=job_dispatcher,
                )
            )
        await asyncio.wait_for(entered.wait(), timeout=5)
        if trigger_first:
            waiter_task = asyncio.create_task(
                update_job(
                    waiter,
                    current_user=waiter_actor,
                    job_id=job_id,
                    payload=JobUpdateRequest(raw_content="update after trigger"),
                    dispatcher=job_dispatcher,
                )
            )
        else:
            waiter_task = asyncio.create_task(
                calculate_matches(
                    waiter,
                    current_user=waiter_actor,
                    payload=request,
                    dispatcher=match_dispatcher,
                )
            )
        assert await wait_for_blocker(
            observer,
            waiter_pid=waiter_pid,
            blocker_pid=winner_pid,
        ) == (winner_pid,)
        release.set()
        await asyncio.wait_for(winner_task, timeout=5)
        if trigger_first:
            await asyncio.wait_for(waiter_task, timeout=5)
        else:
            with pytest.raises(APIError) as not_ready:
                await asyncio.wait_for(waiter_task, timeout=5)
            assert not_ready.value.code == "JOB_NOT_READY"
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
        match = await verify.scalar(
            select(MatchResult).where(
                MatchResult.job_id == job_id,
                MatchResult.resume_id == resume_id,
            )
        )
        if trigger_first:
            assert match is not None
            assert len(match_dispatcher.calls) == 1
            old_payload = match_dispatcher.calls[0]
            assert old_payload[0] == match.id
            assert match.generation == old_payload[1] + 1
            assert (match.resume_revision, match.job_revision, match.status) == (
                old_payload[2],
                8,
                "PENDING",
            )
        elif existing_pair:
            assert match is not None and match.job_revision == 8
            assert match_dispatcher.calls == []
        else:
            assert match is None
            assert match_dispatcher.calls == []

    if trigger_first:
        stale = await process_match_task(*old_payload, session_factory=harness.factory)
        assert stale is MatchTaskOutcome.DISCARDED


def terminal_values(status: str) -> dict[str, Any]:
    completed = status == "COMPLETED"
    now = datetime.now(UTC)
    return {
        "status": status,
        "overall_score": Decimal("80.00") if completed else None,
        "skill_score": Decimal("81.00") if completed else None,
        "semantic_score": Decimal("82.00") if completed else None,
        "experience_score": Decimal("83.00") if completed else None,
        "matched_skills": [{"skill_id": 1}] if completed else [],
        "missing_skills": [],
        "gap_analysis_summary": "terminal gap" if completed else None,
        "embedding_model": BGE_M3_MODEL_NAME if completed else None,
        "embedding_preprocessing_version": "resume-text-v1" if completed else None,
        "error_message": None if completed else "controlled terminal failure",
        "calculated_at": now if completed else None,
        "updated_at": now,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["COMPLETED", "FAILED"])
@pytest.mark.parametrize("terminal_first", [True, False], ids=["terminal-first", "update-first"])
async def test_match_terminal_and_raw_update_serialize_with_claimed_current_payload(
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
        payload = (
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
            match_id=payload[0],
            expected_generation=payload[1],
            expected_resume_revision=payload[2],
            expected_job_revision=payload[3],
            algorithm_version=payload[4],
        )

    values = terminal_values(status)
    if terminal_first:
        assert await _terminal_update(
            harness.factory,
            match_id=payload[0],
            expected_generation=payload[1],
            expected_resume_revision=payload[2],
            expected_job_revision=payload[3],
            algorithm_version=payload[4],
            values=values,
        )
        async with harness.factory() as terminal_verify:
            terminal_match = await terminal_verify.get(MatchResult, match_id)
            assert terminal_match is not None and terminal_match.status == status
            if status == "COMPLETED":
                assert terminal_match.overall_score == Decimal("80.00")
                assert terminal_match.embedding_model == BGE_M3_MODEL_NAME
                assert terminal_match.calculated_at is not None
            else:
                assert terminal_match.error_message == "controlled terminal failure"
                assert terminal_match.overall_score is None
        response = await harness.client.put(
            f"/api/v1/jobs/{job_id}",
            headers=harness.headers("hr"),
            json={"raw_content": f"raw after match terminal {status}"},
        )
        assert response.status_code == 200
    else:
        entered = asyncio.Event()
        release = asyncio.Event()
        bind = harness.factory.kw["bind"]
        updater = CommitGateSession(
            bind=bind,
            expire_on_commit=False,
            commit_entered=entered,
            commit_release=release,
        )
        terminal_session = harness.factory()
        terminal_factory = ExistingSessionFactory(terminal_session)
        observer = harness.factory()
        update_task: asyncio.Task[Any] | None = None
        terminal_task: asyncio.Task[bool] | None = None
        try:
            actor = await load_actor(updater, harness.users["hr"])
            update_pid = await updater.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(update_pid, int)
            update_task = asyncio.create_task(
                update_job(
                    updater,
                    current_user=actor,
                    job_id=job_id,
                    payload=JobUpdateRequest(raw_content=f"raw before match terminal {status}"),
                    dispatcher=RecordingDispatcher(),
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=5)
            terminal_task = asyncio.create_task(
                _terminal_update(
                    terminal_factory,
                    match_id=payload[0],
                    expected_generation=payload[1],
                    expected_resume_revision=payload[2],
                    expected_job_revision=payload[3],
                    algorithm_version=payload[4],
                    values=values,
                )
            )
            await asyncio.wait_for(terminal_factory.entered.wait(), timeout=5)
            assert terminal_factory.pid is not None
            assert await wait_for_blocker(
                observer,
                waiter_pid=terminal_factory.pid,
                blocker_pid=update_pid,
            ) == (update_pid,)
            release.set()
            await asyncio.wait_for(update_task, timeout=5)
            assert not await asyncio.wait_for(terminal_task, timeout=5)
        finally:
            release.set()
            tasks = [task for task in (update_task, terminal_task) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await updater.close()
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
@pytest.mark.parametrize("resume_first", [True, False], ids=["resume-first", "job-first"])
async def test_uc09_manual_resume_edit_and_raw_job_update_serialize_both_orders(
    job_update_postgres: JobUpdateHarness,
    resume_first: bool,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    resume_id = harness.resumes[0]
    match_id = harness.matches[0]
    async with harness.factory() as before_session:
        before_resume = await before_session.get(Resume, resume_id)
        before_match = await before_session.get(MatchResult, match_id)
        assert before_resume is not None and before_match is not None
        source_snapshot = (
            before_resume.file_name,
            before_resume.storage_key,
            before_resume.file_size,
            before_resume.mime_type,
            before_resume.create_request_fingerprint,
            before_resume.raw_text,
        )
        old_resume_revision = before_resume.revision
        old_generation = before_match.generation

    parsed_payload = ResumeParsedDataUpdate.model_validate(
        {
            "candidate_profile": {"full_name": "UC09 race candidate"},
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
    dispatcher = RecordingDispatcher()
    winner_task: asyncio.Task[Any] | None = None
    waiter_task: asyncio.Task[Any] | None = None
    try:
        winner_actor = await load_actor(winner, harness.users["hr"])
        waiter_actor = await load_actor(waiter, harness.users["hr"])
        winner_pid = await winner.scalar(text("SELECT pg_backend_pid()"))
        waiter_pid = await waiter.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(winner_pid, int) and isinstance(waiter_pid, int)
        if resume_first:
            winner_task = asyncio.create_task(
                update_resume_parsed_data(
                    winner,
                    current_user=winner_actor,
                    resume_id=resume_id,
                    payload=parsed_payload,
                    embedding_provider=ControlledEmbeddingProvider(),
                )
            )
        else:
            winner_task = asyncio.create_task(
                update_job(
                    winner,
                    current_user=winner_actor,
                    job_id=job_id,
                    payload=JobUpdateRequest(raw_content="job mutation in UC09 race"),
                    dispatcher=dispatcher,
                )
            )
        await asyncio.wait_for(entered.wait(), timeout=5)
        if resume_first:
            waiter_task = asyncio.create_task(
                update_job(
                    waiter,
                    current_user=waiter_actor,
                    job_id=job_id,
                    payload=JobUpdateRequest(raw_content="job mutation after UC09"),
                    dispatcher=dispatcher,
                )
            )
        else:
            waiter_task = asyncio.create_task(
                update_resume_parsed_data(
                    waiter,
                    current_user=waiter_actor,
                    resume_id=resume_id,
                    payload=parsed_payload,
                    embedding_provider=ControlledEmbeddingProvider(),
                )
            )
        assert await wait_for_blocker(
            observer,
            waiter_pid=waiter_pid,
            blocker_pid=winner_pid,
        ) == (winner_pid,)
        release.set()
        await asyncio.wait_for(asyncio.gather(winner_task, waiter_task), timeout=10)
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
        ) == source_snapshot
        assert resume.is_manually_edited is True
        profile = await verify.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == resume_id)
        )
        assert profile is not None and profile.full_name == "UC09 race candidate"


@pytest.mark.asyncio
@pytest.mark.parametrize("competitor", ["trigger", "criteria", "raw"])
@pytest.mark.parametrize("job_a_first", [True, False], ids=["job-a-first", "job-b-first"])
async def test_cross_job_operations_share_ordered_resumes_without_deadlock_or_lost_snapshots(
    job_update_postgres: JobUpdateHarness,
    competitor: str,
    job_a_first: bool,
) -> None:
    harness = job_update_postgres
    job_a = harness.jobs["parsed"]
    job_b = harness.jobs["other"]
    shared_resumes = tuple(sorted(harness.resumes[:2], key=lambda item: item.int))
    reverse_match_ids = tuple(sorted((uuid.uuid4(), uuid.uuid4()), key=lambda item: item.int))
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
            resume.id: resume
            for resume in (
                await setup.scalars(select(Resume).where(Resume.id.in_(shared_resumes)))
            ).all()
        }
        b_matches = (
            make_match(persisted_b, resume_rows[shared_resumes[1]], 50, "PENDING"),
            make_match(persisted_b, resume_rows[shared_resumes[0]], 51, "PENDING"),
        )
        b_matches[0].id = reverse_match_ids[0]
        b_matches[1].id = reverse_match_ids[1]
        setup.add_all(b_matches)
        await setup.commit()
        initial_generations = {
            row.id: row.generation
            for row in (
                await setup.scalars(
                    select(MatchResult).where(MatchResult.job_id.in_((job_a, job_b)))
                )
            ).all()
        }

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

    async def run_a(session: AsyncSession, actor: User) -> Any:
        return await update_job(
            session,
            current_user=actor,
            job_id=job_a,
            payload=JobUpdateRequest(raw_content=f"cross-job A against {competitor}"),
            dispatcher=RecordingDispatcher(),
        )

    async def run_b(session: AsyncSession, actor: User) -> Any:
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
        return await update_job(
            session,
            current_user=actor,
            job_id=job_b,
            payload=JobUpdateRequest(raw_content="cross-job B raw"),
            dispatcher=RecordingDispatcher(),
        )

    try:
        winner_actor = await load_actor(winner, harness.users["admin"])
        waiter_actor = await load_actor(waiter, harness.users["admin"])
        winner_pid = await winner.scalar(text("SELECT pg_backend_pid()"))
        waiter_pid = await waiter.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(winner_pid, int) and isinstance(waiter_pid, int)
        winner_task = asyncio.create_task(
            run_a(winner, winner_actor) if job_a_first else run_b(winner, winner_actor)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        waiter_task = asyncio.create_task(
            run_b(waiter, waiter_actor) if job_a_first else run_a(waiter, waiter_actor)
        )
        assert await wait_for_blocker(
            observer,
            waiter_pid=waiter_pid,
            blocker_pid=winner_pid,
        ) == (winner_pid,)
        release.set()
        await asyncio.wait_for(asyncio.gather(winner_task, waiter_task), timeout=10)
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
        affected_resume_ids = {row.resume_id for row in rows}
        resumes = {
            row.id: row
            for row in (
                await verify.scalars(select(Resume).where(Resume.id.in_(affected_resume_ids)))
            ).all()
        }
        assert jobs[job_a].revision == 8
        expected_b_revision = 8 if competitor in {"criteria", "raw"} else 7
        assert jobs[job_b].revision == expected_b_revision
        for row in rows:
            assert row.generation == initial_generations[row.id] + 1
            assert row.job_revision == jobs[row.job_id].revision
            assert row.resume_revision == resumes[row.resume_id].revision
            assert row.status == "PENDING"


@pytest.mark.asyncio
async def test_post_commit_publication_does_not_hold_lock_and_keeps_immutable_revisions(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    first_dispatcher = RecordingDispatcher()
    first_dispatcher.pause = True
    second_dispatcher = RecordingDispatcher()
    async with harness.factory() as first, harness.factory() as second:
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        assert first_pid is not None and second_pid is not None and first_pid != second_pid
        first_actor = await load_actor(first, harness.users["hr"])
        second_actor = await load_actor(second, harness.users["hr"])
        first_task = asyncio.create_task(
            update_job(
                first,
                current_user=first_actor,
                job_id=job_id,
                payload=JobUpdateRequest(raw_content="first concurrent content"),
                dispatcher=first_dispatcher,
            )
        )
        try:
            await asyncio.wait_for(first_dispatcher.entered.wait(), timeout=5)
            second_result = await asyncio.wait_for(
                update_job(
                    second,
                    current_user=second_actor,
                    job_id=job_id,
                    payload=JobUpdateRequest(raw_content="second concurrent content"),
                    dispatcher=second_dispatcher,
                ),
                timeout=5,
            )
            assert second_result.revision == 9
            first_dispatcher.release.set()
            first_result = await asyncio.wait_for(first_task, timeout=5)
        finally:
            first_dispatcher.release.set()
            if not first_task.done():
                first_task.cancel()
            await asyncio.gather(first_task, return_exceptions=True)

    assert first_result.revision == 8
    assert first_dispatcher.calls == [(job_id, 8)]
    assert second_dispatcher.calls == [(job_id, 9)]
    async with harness.factory() as verify:
        job = await verify.get(JobDescription, job_id)
        assert job is not None
        assert (job.revision, job.raw_content) == (9, "second concurrent content")
    assert (
        await process_job_parse_task(
            job_id,
            8,
            session_factory=harness.factory,
            embedding_provider=ControlledEmbeddingProvider(),
        )
        is JobParseTaskOutcome.DISCARDED
    )


@pytest.mark.asyncio
async def test_second_same_raw_value_decides_after_lock_without_extra_computation(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    first_dispatcher = RecordingDispatcher()
    first_dispatcher.pause = True
    second_dispatcher = RecordingDispatcher()
    async with harness.factory() as first, harness.factory() as second:
        first_actor = await load_actor(first, harness.users["hr"])
        second_actor = await load_actor(second, harness.users["hr"])
        payload = JobUpdateRequest(raw_content="shared concurrent content")
        first_task = asyncio.create_task(
            update_job(
                first,
                current_user=first_actor,
                job_id=job_id,
                payload=payload,
                dispatcher=first_dispatcher,
            )
        )
        try:
            await asyncio.wait_for(first_dispatcher.entered.wait(), timeout=5)
            second_result = await asyncio.wait_for(
                update_job(
                    second,
                    current_user=second_actor,
                    job_id=job_id,
                    payload=payload,
                    dispatcher=second_dispatcher,
                ),
                timeout=5,
            )
            first_dispatcher.release.set()
            await asyncio.wait_for(first_task, timeout=5)
        finally:
            first_dispatcher.release.set()
            if not first_task.done():
                first_task.cancel()
            await asyncio.gather(first_task, return_exceptions=True)
    assert second_result.revision == 8
    assert first_dispatcher.calls == [(job_id, 8)]
    assert second_dispatcher.calls == []


@pytest.mark.asyncio
async def test_stale_attached_identity_uses_current_locked_job_resume_and_match_state(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    resume_id = harness.resumes[0]
    match_id = harness.matches[0]
    dispatcher = RecordingDispatcher()
    async with harness.factory() as editor, harness.factory() as external:
        actor = await load_actor(editor, harness.users["hr"])
        cached_job = await editor.get(JobDescription, job_id)
        cached_resume = await editor.get(Resume, resume_id)
        cached_match = await editor.get(MatchResult, match_id)
        assert cached_job is not None and cached_resume is not None and cached_match is not None
        cached_raw = cached_job.raw_content
        cached_resume_revision = cached_resume.revision
        cached_generation = cached_match.generation

        await external.execute(
            update(Resume).where(Resume.id == resume_id).values(revision=cached_resume_revision + 5)
        )
        await external.execute(
            update(JobDescription)
            .where(JobDescription.id == job_id)
            .values(
                raw_content="database current raw",
                revision=11,
                status="DRAFT",
                parsing_status="PENDING",
                is_criteria_verified=False,
                job_embedding=None,
                embedding_model=None,
                embedding_preprocessing_version=None,
                parsed_at=None,
            )
        )
        await external.execute(
            update(MatchResult)
            .where(MatchResult.id == match_id)
            .values(
                generation=cached_generation + 7,
                resume_revision=cached_resume_revision + 5,
                job_revision=11,
                status="PENDING",
                overall_score=None,
                skill_score=None,
                semantic_score=None,
                experience_score=None,
                matched_skills=[],
                missing_skills=[],
                gap_analysis_summary=None,
                error_message=None,
                embedding_model=None,
                embedding_preprocessing_version=None,
                calculated_at=None,
            )
        )
        await external.commit()
        assert cached_job.raw_content == cached_raw
        assert cached_resume.revision == cached_resume_revision
        assert cached_match.generation == cached_generation
        negative_job = await editor.scalar(
            select(JobDescription).where(JobDescription.id == job_id).with_for_update()
        )
        negative_resume = await editor.scalar(
            select(Resume).where(Resume.id == resume_id).with_for_update()
        )
        negative_match = await editor.scalar(
            select(MatchResult).where(MatchResult.id == match_id).with_for_update()
        )
        assert negative_job is cached_job and negative_job.revision == 7
        assert (
            negative_resume is cached_resume and negative_resume.revision == cached_resume_revision
        )
        assert negative_match is cached_match and negative_match.generation == cached_generation

        result = await update_job(
            editor,
            current_user=actor,
            job_id=job_id,
            payload=JobUpdateRequest(raw_content=cached_raw),
            dispatcher=dispatcher,
        )
        assert result.revision == 12

    assert dispatcher.calls == [(job_id, 12)]
    async with harness.factory() as verify:
        resume = await verify.get(Resume, resume_id)
        match = await verify.get(MatchResult, match_id)
        assert resume is not None and match is not None
        assert match.generation == cached_generation + 8
        assert (match.resume_revision, match.job_revision) == (resume.revision, 12)


@pytest.mark.asyncio
async def test_stale_cached_raw_difference_does_not_create_false_reparse_when_database_matches(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    dispatcher = RecordingDispatcher()
    async with harness.factory() as editor, harness.factory() as external:
        actor = await load_actor(editor, harness.users["hr"])
        cached_job = await editor.get(JobDescription, job_id)
        assert cached_job is not None
        old_raw = cached_job.raw_content
        await external.execute(
            update(JobDescription)
            .where(JobDescription.id == job_id)
            .values(raw_content="already current database raw", revision=11)
        )
        await external.commit()
        assert cached_job.raw_content == old_raw
        negative_control = await editor.scalar(
            select(JobDescription).where(JobDescription.id == job_id).with_for_update()
        )
        assert negative_control is cached_job
        assert negative_control.raw_content != "already current database raw"

        result = await update_job(
            editor,
            current_user=actor,
            job_id=job_id,
            payload=JobUpdateRequest(raw_content="already current database raw"),
            dispatcher=dispatcher,
        )
    assert result.revision == 11
    assert result.raw_content == "already current database raw"
    assert dispatcher.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh_guard", ["deleted", "ownership"])
async def test_final_locking_read_uses_fresh_deleted_and_ownership_guards(
    job_update_postgres: JobUpdateHarness,
    fresh_guard: str,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    dispatcher = RecordingDispatcher()
    async with harness.factory() as editor, harness.factory() as external:
        actor = await load_actor(editor, harness.users["hr"])
        cached = await editor.get(JobDescription, job_id)
        assert cached is not None and cached.is_deleted is False
        if fresh_guard == "deleted":
            await external.execute(
                update(JobDescription)
                .where(JobDescription.id == job_id)
                .values(is_deleted=True, deleted_at=datetime.now(UTC))
            )
        else:
            await external.execute(
                update(JobDescription)
                .where(JobDescription.id == job_id)
                .values(recruiter_id=harness.users["other_hr"])
            )
        await external.commit()
        assert cached.is_deleted is False and cached.recruiter_id == harness.users["hr"]
        with pytest.raises(APIError) as hidden:
            await update_job(
                editor,
                current_user=actor,
                job_id=job_id,
                payload=JobUpdateRequest(title="must stay hidden"),
                dispatcher=dispatcher,
            )
        assert hidden.value.status_code == 404
    assert dispatcher.calls == []


@pytest.mark.asyncio
async def test_original_create_fingerprint_replay_survives_edit_and_redispatches_current_revision(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    key = uuid.uuid4()
    original_payload = {
        "title": "Created title",
        "job_level": "MID",
        "location": "Hanoi",
        "raw_content": "Original create raw",
    }
    headers = harness.headers("hr") | {"Idempotency-Key": str(key)}
    created = await harness.client.post("/api/v1/jobs", headers=headers, json=original_payload)
    assert created.status_code == 201
    job_id = uuid.UUID(created.json()["data"]["id"])

    edited = await harness.client.put(
        f"/api/v1/jobs/{job_id}",
        headers=harness.headers("hr"),
        json={"title": "Edited title", "raw_content": "Edited current raw"},
    )
    assert edited.status_code == 200
    assert edited.json()["data"]["revision"] == 2

    replay = await harness.client.post("/api/v1/jobs", headers=headers, json=original_payload)
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == str(job_id)
    assert replay.json()["data"]["revision"] == 2
    assert replay.json()["data"]["raw_content"] == "Edited current raw"

    conflict = await harness.client.post(
        "/api/v1/jobs",
        headers=headers,
        json=original_payload | {"title": "Edited title", "raw_content": "Edited current raw"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert harness.dispatcher.calls[-3:] == [(job_id, 1), (job_id, 2), (job_id, 2)]


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_complete_job_and_match_mutation_without_publication(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    job_id = harness.jobs["parsed"]
    async with harness.factory() as snapshot_session:
        job_before = await snapshot_session.get(JobDescription, job_id)
        assert job_before is not None
        before = snapshot(job_before)
        matches_before = {
            match_id: snapshot(await snapshot_session.get(MatchResult, match_id))
            for match_id in harness.matches
        }

    dispatcher = RecordingDispatcher()
    async with harness.factory() as session:
        actor = await load_actor(session, harness.users["hr"])

        def fail_before_commit(_: Any) -> None:
            raise SQLAlchemyError("controlled pre-commit failure")

        event.listen(session.sync_session, "before_commit", fail_before_commit)
        try:
            with pytest.raises(SQLAlchemyError):
                await update_job(
                    session,
                    current_user=actor,
                    job_id=job_id,
                    payload=JobUpdateRequest(raw_content="must rollback"),
                    dispatcher=dispatcher,
                )
        finally:
            event.remove(session.sync_session, "before_commit", fail_before_commit)
            await session.rollback()

    assert dispatcher.calls == []
    async with harness.factory() as verify:
        job_after = await verify.get(JobDescription, job_id)
        assert job_after is not None and snapshot(job_after) == before
        for match_id in harness.matches:
            match = await verify.get(MatchResult, match_id)
            assert match is not None and snapshot(match) == matches_before[match_id]


@pytest.mark.asyncio
async def test_locking_reads_precede_autoflush_with_constraint_sensitive_pending_state(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    dispatcher = RecordingDispatcher()
    observations: list[str] = []
    async with harness.factory() as session:
        actor = await load_actor(session, harness.users["hr"])
        duplicate = make_user(uuid.uuid4().hex, "duplicate", "HR")
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
                await update_job(
                    session,
                    current_user=actor,
                    job_id=harness.jobs["parsed"],
                    payload=JobUpdateRequest(raw_content="constraint-sensitive raw"),
                    dispatcher=dispatcher,
                )
        finally:
            event.remove(session.sync_session, "before_flush", observe_flush)
            event.remove(sync_engine, "before_cursor_execute", observe_statement)
            await session.rollback()

    assert observations == ["job_lock", "resume_lock", "match_lock", "flush"]
    assert dispatcher.calls == []


@pytest.mark.asyncio
async def test_no_autoflush_negative_control_flushes_constraint_failure_before_job_lock(
    job_update_postgres: JobUpdateHarness,
) -> None:
    harness = job_update_postgres
    observations: list[str] = []
    async with harness.factory() as session:
        actor = await load_actor(session, harness.users["hr"])
        duplicate = make_user(uuid.uuid4().hex, "negative-control", "HR")
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
                await update_job(
                    AutoflushEnabledSession(session),  # type: ignore[arg-type]
                    current_user=actor,
                    job_id=harness.jobs["parsed"],
                    payload=JobUpdateRequest(raw_content="negative-control raw"),
                    dispatcher=RecordingDispatcher(),
                )
        finally:
            event.remove(session.sync_session, "before_flush", observe_flush)
            event.remove(sync_engine, "before_cursor_execute", observe_statement)
            await session.rollback()

    assert observations == ["flush"]
