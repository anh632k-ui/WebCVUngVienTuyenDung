from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
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

from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.engine_factory import create_engine
from app.core.security import create_access_token
from app.core.version_guard import claim_job_revision, claim_match_generation
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume
from app.models.skill import JobSkill, Skill
from app.models.user import User
from app.schemas.job_schema import JobUpdateRequest
from app.services.job_dispatcher import get_job_parse_dispatcher
from app.services.job_service import update_job
from app.workers.job_parse_worker import JobParseTaskOutcome, process_job_parse_task
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


class ControlledEmbeddingProvider:
    model_name = BGE_M3_MODEL_NAME
    dimension = BGE_M3_EMBEDDING_DIMENSION

    async def embed(self, text_value: str) -> list[float]:
        assert text_value
        return [0.125] * self.dimension


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
        resume_revision=1,
        job_revision=1,
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

    async with harness.factory() as stale_job_worker:
        assert not await claim_job_revision(
            stale_job_worker,
            job_id=job_id,
            expected_revision=7,
        )
    first_match_id = harness.matches[0]
    async with harness.factory() as stale_match_worker:
        assert not await claim_match_generation(
            stale_match_worker,
            match_id=first_match_id,
            expected_generation=generations[first_match_id],
            expected_resume_revision=1,
            expected_job_revision=1,
            algorithm_version="hybrid-v1",
        )


@pytest.mark.asyncio
async def test_raw_updates_all_parse_states_no_matches_and_dispatch_failure_is_best_effort(
    job_update_postgres: JobUpdateHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = job_update_postgres
    harness.dispatcher.fail = True
    for state in ("pending", "processing", "failed"):
        job_id = harness.jobs[state]
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

        def observe_statement(execute_state: Any) -> None:
            statement = str(execute_state.statement)
            if "FOR UPDATE" not in statement:
                return
            if "job_descriptions" in statement:
                observations.append("job_lock")
            elif "resumes" in statement:
                observations.append("resume_lock")
            elif "match_results" in statement:
                observations.append("match_lock")

        event.listen(session.sync_session, "before_flush", observe_flush)
        event.listen(session.sync_session, "do_orm_execute", observe_statement)
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
            event.remove(session.sync_session, "do_orm_execute", observe_statement)
            await session.rollback()

    assert observations == ["job_lock", "resume_lock", "match_lock", "flush"]
    assert dispatcher.calls == []
