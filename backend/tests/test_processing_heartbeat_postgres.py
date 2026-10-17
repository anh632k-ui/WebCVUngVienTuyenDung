from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.processing_fencing import (
    ProcessingAttemptGuard,
    ProcessingHeartbeatOutcome,
    ProcessingResourceKind,
    renew_processing_attempt_lease,
)
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume

_EXPECTED_DATABASE = "r3c_phase4a_heartbeat"
_EXPECTED_USER = "r3c_phase4a_ci"


def _ci_database_url() -> str | None:
    if os.getenv("R3C_PHASE4A_POSTGRES_INTEGRATION") != "true":
        return None
    if os.getenv("GITHUB_ACTIONS") != "true":
        return None
    if os.getenv("RUNNER_ENVIRONMENT") != "github-hosted":
        return None
    raw_url = os.getenv("R3C_PHASE4A_DATABASE_URL", "")
    parsed = urlsplit(raw_url)
    if not (
        parsed.scheme == "postgresql+asyncpg"
        and parsed.hostname == "127.0.0.1"
        and parsed.port == 5432
        and parsed.username == _EXPECTED_USER
        and parsed.path == f"/{_EXPECTED_DATABASE}"
        and not parsed.query
        and not parsed.fragment
    ):
        return None
    return raw_url


CI_DATABASE_URL = _ci_database_url()
if os.getenv("R3C_PHASE4A_POSTGRES_INTEGRATION") == "true" and CI_DATABASE_URL is None:
    raise RuntimeError("Phase 4A PostgreSQL opt-in is set but the isolated CI guard failed")
pytestmark = pytest.mark.skipif(
    CI_DATABASE_URL is None,
    reason="Phase 4A PostgreSQL QA runs only on its disposable GitHub-hosted database",
)


@dataclass(frozen=True, slots=True)
class HeartbeatRows:
    candidate_id: uuid.UUID
    recruiter_id: uuid.UUID
    resume_id: uuid.UUID
    job_id: uuid.UUID
    match_id: uuid.UUID
    skill_id: int
    resume_guard: ProcessingAttemptGuard
    job_guard: ProcessingAttemptGuard
    match_guard: ProcessingAttemptGuard


@pytest_asyncio.fixture
async def phase4a_engine() -> AsyncEngine:
    assert CI_DATABASE_URL is not None
    engine = create_async_engine(CI_DATABASE_URL, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT current_database()")) == _EXPECTED_DATABASE
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def heartbeat_rows(phase4a_engine: AsyncEngine) -> HeartbeatRows:
    candidate_id, recruiter_id = uuid.uuid4(), uuid.uuid4()
    resume_id, job_id, match_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    resume_owner, resume_activation = uuid.uuid4(), uuid.uuid4()
    job_owner, job_activation = uuid.uuid4(), uuid.uuid4()
    match_owner, match_activation = uuid.uuid4(), uuid.uuid4()
    async with AsyncSession(phase4a_engine) as session:
        await session.execute(
            text(
                """
                INSERT INTO users
                    (id, email, password_hash, full_name, role)
                VALUES
                    (:candidate_id, :candidate_email, 'unused', 'Candidate', 'CANDIDATE'),
                    (:recruiter_id, :recruiter_email, 'unused', 'Recruiter', 'HR')
                """
            ),
            {
                "candidate_id": candidate_id,
                "candidate_email": f"phase4a-candidate-{candidate_id}@example.test",
                "recruiter_id": recruiter_id,
                "recruiter_email": f"phase4a-recruiter-{recruiter_id}@example.test",
            },
        )
        skill_id = await session.scalar(
            text(
                """
                INSERT INTO skills (name, normalized_name, skill_kind, category)
                VALUES (:name, :normalized_name, 'HARD', 'phase4a')
                RETURNING id
                """
            ),
            {"name": f"Phase4A-{resume_id}", "normalized_name": f"phase4a-{resume_id}"},
        )
        assert skill_id is not None
        await session.execute(
            text(
                """
                INSERT INTO resumes (
                    id, owner_user_id, file_name, storage_key, file_size, mime_type,
                    create_request_fingerprint, revision, parsing_status,
                    processing_attempt_epoch, processing_owner_token,
                    processing_activation_token, processing_heartbeat_at,
                    processing_lease_expires_at
                ) VALUES (
                    :id, :owner, 'phase4a.pdf', :storage_key, 1, 'application/pdf',
                    :fingerprint, 1, 'PROCESSING', 7, :attempt_owner, :activation,
                    clock_timestamp(), clock_timestamp() + interval '30 minutes'
                )
                """
            ),
            {
                "id": resume_id,
                "owner": candidate_id,
                "storage_key": f"phase4a/{resume_id}",
                "fingerprint": resume_id.hex * 2,
                "attempt_owner": resume_owner,
                "activation": resume_activation,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO job_descriptions (
                    id, recruiter_id, title, job_level, raw_content,
                    create_request_fingerprint, revision, parsing_status,
                    processing_attempt_epoch, processing_owner_token,
                    processing_activation_token, processing_heartbeat_at,
                    processing_lease_expires_at
                ) VALUES (
                    :id, :recruiter, 'Phase 4A', 'MID', 'heartbeat fixture',
                    :fingerprint, 1, 'PROCESSING', 8, :attempt_owner, :activation,
                    clock_timestamp(), clock_timestamp() + interval '30 minutes'
                )
                """
            ),
            {
                "id": job_id,
                "recruiter": recruiter_id,
                "fingerprint": job_id.hex * 2,
                "attempt_owner": job_owner,
                "activation": job_activation,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO resume_skills (resume_id, skill_id, years_of_experience)
                VALUES (:resume_id, :skill_id, 2.0)
                """
            ),
            {"resume_id": resume_id, "skill_id": skill_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO job_skills (job_id, skill_id, importance, min_years_required)
                VALUES (:job_id, :skill_id, 'MANDATORY', 1.0)
                """
            ),
            {"job_id": job_id, "skill_id": skill_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO match_results (
                    id, job_id, resume_id, generation, resume_revision, job_revision,
                    algorithm_version, status, processing_attempt_epoch,
                    processing_owner_token, processing_activation_token,
                    processing_heartbeat_at, processing_lease_expires_at
                ) VALUES (
                    :id, :job_id, :resume_id, 3, 1, 1, 'hybrid-v1', 'PROCESSING',
                    9, :attempt_owner, :activation, clock_timestamp(),
                    clock_timestamp() + interval '30 minutes'
                )
                """
            ),
            {
                "id": match_id,
                "job_id": job_id,
                "resume_id": resume_id,
                "attempt_owner": match_owner,
                "activation": match_activation,
            },
        )
        await session.commit()

    rows = HeartbeatRows(
        candidate_id,
        recruiter_id,
        resume_id,
        job_id,
        match_id,
        skill_id,
        ProcessingAttemptGuard(
            ProcessingResourceKind.RESUME,
            resume_id,
            1,
            7,
            resume_owner,
            resume_activation,
        ),
        ProcessingAttemptGuard(
            ProcessingResourceKind.JOB,
            job_id,
            1,
            8,
            job_owner,
            job_activation,
        ),
        ProcessingAttemptGuard(
            ProcessingResourceKind.MATCH,
            match_id,
            3,
            9,
            match_owner,
            match_activation,
            resume_revision=1,
            job_revision=1,
            algorithm_version="hybrid-v1",
        ),
    )
    try:
        yield rows
    finally:
        async with AsyncSession(phase4a_engine) as session:
            await session.execute(
                text("DELETE FROM users WHERE id IN (:candidate_id, :recruiter_id)"),
                {"candidate_id": candidate_id, "recruiter_id": recruiter_id},
            )
            await session.execute(text("DELETE FROM skills WHERE id = :id"), {"id": skill_id})
            await session.commit()


def _resource(rows: HeartbeatRows, kind: ProcessingResourceKind) -> tuple[Any, uuid.UUID, Any]:
    if kind is ProcessingResourceKind.RESUME:
        return Resume, rows.resume_id, rows.resume_guard
    if kind is ProcessingResourceKind.JOB:
        return JobDescription, rows.job_id, rows.job_guard
    return MatchResult, rows.match_id, rows.match_guard


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ProcessingResourceKind))
async def test_valid_heartbeat_renews_without_shortening_or_business_mutation(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
    kind: ProcessingResourceKind,
) -> None:
    model, resource_id, guard = _resource(heartbeat_rows, kind)
    async with AsyncSession(phase4a_engine, expire_on_commit=False) as session:
        before = await session.get(model, resource_id)
        assert before is not None
        original = {
            key: value
            for key, value in before.__dict__.items()
            if not key.startswith("_sa_")
            and key not in {"processing_heartbeat_at", "processing_lease_expires_at"}
        }
        previous_lease = before.processing_lease_expires_at
        aggregate_before = (
            await session.execute(
                text(
                    """
                    SELECT
                      (SELECT jsonb_agg(to_jsonb(rs) ORDER BY rs.id)::text
                         FROM resume_skills rs WHERE rs.resume_id = :resume_id),
                      (SELECT jsonb_agg(to_jsonb(js) ORDER BY js.id)::text
                         FROM job_skills js WHERE js.job_id = :job_id)
                    """
                ),
                {"resume_id": heartbeat_rows.resume_id, "job_id": heartbeat_rows.job_id},
            )
        ).one()

    async with AsyncSession(phase4a_engine) as session:
        outcome = await renew_processing_attempt_lease(session, guard, lease_seconds=60)
    assert outcome is ProcessingHeartbeatOutcome.RENEWED

    async with AsyncSession(phase4a_engine, expire_on_commit=False) as session:
        after = await session.get(model, resource_id)
        assert after is not None
        unchanged = {
            key: value
            for key, value in after.__dict__.items()
            if not key.startswith("_sa_")
            and key not in {"processing_heartbeat_at", "processing_lease_expires_at"}
        }
        assert unchanged == original
        assert after.processing_lease_expires_at >= previous_lease
        aggregate_after = (
            await session.execute(
                text(
                    """
                    SELECT
                      (SELECT jsonb_agg(to_jsonb(rs) ORDER BY rs.id)::text
                         FROM resume_skills rs WHERE rs.resume_id = :resume_id),
                      (SELECT jsonb_agg(to_jsonb(js) ORDER BY js.id)::text
                         FROM job_skills js WHERE js.job_id = :job_id)
                    """
                ),
                {"resume_id": heartbeat_rows.resume_id, "job_id": heartbeat_rows.job_id},
            )
        ).one()
        assert aggregate_after == aggregate_before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ProcessingResourceKind))
@pytest.mark.parametrize("mismatch", ["snapshot", "epoch", "owner", "activation"])
async def test_guard_mismatch_never_renews(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
    kind: ProcessingResourceKind,
    mismatch: str,
) -> None:
    _, _, guard = _resource(heartbeat_rows, kind)
    changes: dict[str, Any] = {
        "snapshot": {"snapshot": guard.snapshot + 1},
        "epoch": {"attempt_epoch": guard.attempt_epoch + 1},
        "owner": {"owner_token": uuid.uuid4()},
        "activation": {"activation_token": uuid.uuid4()},
    }[mismatch]
    async with AsyncSession(phase4a_engine) as session:
        outcome = await renew_processing_attempt_lease(
            session, replace(guard, **changes), lease_seconds=60
        )
    assert outcome is ProcessingHeartbeatOutcome.LOST_OWNERSHIP


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ProcessingResourceKind))
@pytest.mark.parametrize("disqualification", ["expired", "revoked", "terminal"])
async def test_durable_state_disqualification_never_resurrects_ownership(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
    kind: ProcessingResourceKind,
    disqualification: str,
) -> None:
    model, resource_id, guard = _resource(heartbeat_rows, kind)
    status_column = "status" if kind is ProcessingResourceKind.MATCH else "parsing_status"
    terminal_status = "FAILED"
    error_column = (
        "parsing_error_message" if kind is ProcessingResourceKind.JOB else "error_message"
    )
    if disqualification == "expired":
        clause = "processing_lease_expires_at = clock_timestamp() - interval '1 second'"
    elif disqualification == "revoked":
        clause = (
            "processing_owner_token = NULL, processing_activation_token = NULL, "
            "processing_heartbeat_at = NULL, processing_lease_expires_at = NULL"
        )
    else:
        clause = (
            f"{status_column} = '{terminal_status}', {error_column} = 'terminal', "
            "processing_owner_token = NULL, processing_activation_token = NULL, "
            "processing_heartbeat_at = NULL, processing_lease_expires_at = NULL"
        )
    async with AsyncSession(phase4a_engine) as session:
        await session.execute(
            text(f"UPDATE {model.__tablename__} SET {clause} WHERE id = :id"),
            {"id": resource_id},
        )
        await session.commit()
    async with AsyncSession(phase4a_engine) as session:
        outcome = await renew_processing_attempt_lease(session, guard, lease_seconds=60)
    assert outcome is ProcessingHeartbeatOutcome.LOST_OWNERSHIP


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [ProcessingResourceKind.RESUME, ProcessingResourceKind.JOB])
async def test_soft_deleted_resume_or_job_never_renews(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
    kind: ProcessingResourceKind,
) -> None:
    model, resource_id, guard = _resource(heartbeat_rows, kind)
    async with AsyncSession(phase4a_engine) as session:
        await session.execute(
            text(
                f"UPDATE {model.__tablename__} SET is_deleted = TRUE, "
                "deleted_at = clock_timestamp() WHERE id = :id"
            ),
            {"id": resource_id},
        )
        await session.commit()
    async with AsyncSession(phase4a_engine) as session:
        assert (
            await renew_processing_attempt_lease(session, guard, lease_seconds=60)
            is ProcessingHeartbeatOutcome.LOST_OWNERSHIP
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["PARSED", "FAILED"])
async def test_resume_already_parsed_or_failed_never_renews(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
    terminal_status: str,
) -> None:
    if terminal_status == "PARSED":
        payload = (
            "raw_text = 'parsed', "
            "resume_embedding = array_fill(0::real, ARRAY[1024])::vector, "
            "embedding_model = 'test', embedding_preprocessing_version = 'test', "
            "parsed_at = clock_timestamp(), error_message = NULL"
        )
    else:
        payload = "error_message = 'failed'"
    async with AsyncSession(phase4a_engine) as session:
        await session.execute(
            text(
                f"UPDATE resumes SET parsing_status = :status, {payload}, "
                "processing_owner_token = NULL, processing_activation_token = NULL, "
                "processing_heartbeat_at = NULL, processing_lease_expires_at = NULL "
                "WHERE id = :id"
            ),
            {"status": terminal_status, "id": heartbeat_rows.resume_id},
        )
        await session.commit()
    async with AsyncSession(phase4a_engine) as session:
        assert (
            await renew_processing_attempt_lease(
                session, heartbeat_rows.resume_guard, lease_seconds=60
            )
            is ProcessingHeartbeatOutcome.LOST_OWNERSHIP
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "guard_change"),
    [
        ("UPDATE resumes SET revision = revision + 1 WHERE id = :id", None),
        ("UPDATE job_descriptions SET revision = revision + 1 WHERE id = :id", None),
        (
            "UPDATE resumes SET is_deleted = TRUE, deleted_at = clock_timestamp() WHERE id = :id",
            None,
        ),
        (
            "UPDATE job_descriptions SET is_deleted = TRUE, "
            "deleted_at = clock_timestamp() WHERE id = :id",
            None,
        ),
        (None, {"snapshot": 4}),
        (None, {"resume_revision": 2}),
        (None, {"job_revision": 2}),
        (None, {"algorithm_version": "other"}),
    ],
)
async def test_match_snapshot_and_linked_validity_are_sql_guarded(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
    mutation: str | None,
    guard_change: dict[str, Any] | None,
) -> None:
    guard = heartbeat_rows.match_guard
    if mutation is not None:
        linked_id = heartbeat_rows.resume_id if "resumes" in mutation else heartbeat_rows.job_id
        async with AsyncSession(phase4a_engine) as session:
            await session.execute(text(mutation), {"id": linked_id})
            await session.commit()
    if guard_change is not None:
        guard = replace(guard, **guard_change)
    async with AsyncSession(phase4a_engine) as session:
        assert (
            await renew_processing_attempt_lease(session, guard, lease_seconds=60)
            is ProcessingHeartbeatOutcome.LOST_OWNERSHIP
        )


async def _wait_until_blocked(observer: AsyncSession, backend_pid: int) -> None:
    async with asyncio.timeout(10):
        while True:
            blockers = await observer.scalar(
                text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": backend_pid}
            )
            if blockers:
                return
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_lock_wait_expiry_is_evaluated_with_fresh_database_clock(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
) -> None:
    guard = heartbeat_rows.resume_guard
    lock_holder = AsyncSession(phase4a_engine)
    heartbeat_session = AsyncSession(phase4a_engine)
    observer = AsyncSession(phase4a_engine)
    try:
        await lock_holder.execute(
            text(
                "UPDATE resumes SET processing_lease_expires_at = "
                "clock_timestamp() - interval '1 second' WHERE id = :id"
            ),
            {"id": heartbeat_rows.resume_id},
        )
        backend_pid = await heartbeat_session.scalar(text("SELECT pg_backend_pid()"))
        assert backend_pid is not None
        renewal = asyncio.create_task(
            renew_processing_attempt_lease(heartbeat_session, guard, lease_seconds=60)
        )
        await _wait_until_blocked(observer, backend_pid)
        await lock_holder.commit()
        assert await renewal is ProcessingHeartbeatOutcome.LOST_OWNERSHIP
    finally:
        await lock_holder.close()
        await heartbeat_session.close()
        await observer.close()


@pytest.mark.asyncio
async def test_two_heartbeats_are_atomic_and_never_shorten_lease(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
) -> None:
    async def renew() -> ProcessingHeartbeatOutcome:
        async with AsyncSession(phase4a_engine) as session:
            return await renew_processing_attempt_lease(
                session, heartbeat_rows.job_guard, lease_seconds=60
            )

    assert await asyncio.gather(renew(), renew()) == [
        ProcessingHeartbeatOutcome.RENEWED,
        ProcessingHeartbeatOutcome.RENEWED,
    ]
    async with AsyncSession(phase4a_engine) as session:
        still_future = await session.scalar(
            text(
                "SELECT processing_lease_expires_at > clock_timestamp() "
                "FROM job_descriptions WHERE id = :id"
            ),
            {"id": heartbeat_rows.job_id},
        )
    assert still_future is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ProcessingResourceKind))
async def test_waiting_heartbeat_loses_to_terminal_transition(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
    kind: ProcessingResourceKind,
) -> None:
    model, resource_id, guard = _resource(heartbeat_rows, kind)
    lock_holder = AsyncSession(phase4a_engine)
    heartbeat_session = AsyncSession(phase4a_engine)
    observer = AsyncSession(phase4a_engine)
    status_column = "status" if kind is ProcessingResourceKind.MATCH else "parsing_status"
    error_column = (
        "parsing_error_message" if kind is ProcessingResourceKind.JOB else "error_message"
    )
    try:
        if kind is ProcessingResourceKind.MATCH:
            await lock_holder.execute(
                text("SELECT id FROM job_descriptions WHERE id = :id FOR UPDATE"),
                {"id": heartbeat_rows.job_id},
            )
            await lock_holder.execute(
                text("SELECT id FROM resumes WHERE id = :id FOR UPDATE"),
                {"id": heartbeat_rows.resume_id},
            )
        await lock_holder.execute(
            text(
                f"UPDATE {model.__tablename__} SET {status_column} = 'FAILED', "
                f"{error_column} = 'terminal', processing_owner_token = NULL, "
                "processing_activation_token = NULL, processing_heartbeat_at = NULL, "
                "processing_lease_expires_at = NULL WHERE id = :id"
            ),
            {"id": resource_id},
        )
        backend_pid = await heartbeat_session.scalar(text("SELECT pg_backend_pid()"))
        assert backend_pid is not None
        renewal = asyncio.create_task(
            renew_processing_attempt_lease(heartbeat_session, guard, lease_seconds=60)
        )
        await _wait_until_blocked(observer, backend_pid)
        await lock_holder.commit()
        assert await renewal is ProcessingHeartbeatOutcome.LOST_OWNERSHIP
    finally:
        await lock_holder.close()
        await heartbeat_session.close()
        await observer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "mutation"),
    [
        (
            ProcessingResourceKind.RESUME,
            "revision = revision + 1, processing_owner_token = NULL, "
            "processing_activation_token = NULL, processing_heartbeat_at = NULL, "
            "processing_lease_expires_at = NULL",
        ),
        (
            ProcessingResourceKind.JOB,
            "is_deleted = TRUE, deleted_at = clock_timestamp()",
        ),
    ],
)
async def test_waiting_heartbeat_loses_to_business_invalidation_or_soft_delete(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
    kind: ProcessingResourceKind,
    mutation: str,
) -> None:
    model, resource_id, guard = _resource(heartbeat_rows, kind)
    lock_holder = AsyncSession(phase4a_engine)
    heartbeat_session = AsyncSession(phase4a_engine)
    observer = AsyncSession(phase4a_engine)
    try:
        await lock_holder.execute(
            text(f"UPDATE {model.__tablename__} SET {mutation} WHERE id = :id"),
            {"id": resource_id},
        )
        backend_pid = await heartbeat_session.scalar(text("SELECT pg_backend_pid()"))
        assert backend_pid is not None
        renewal = asyncio.create_task(
            renew_processing_attempt_lease(heartbeat_session, guard, lease_seconds=60)
        )
        await _wait_until_blocked(observer, backend_pid)
        await lock_holder.commit()
        assert await renewal is ProcessingHeartbeatOutcome.LOST_OWNERSHIP
    finally:
        await lock_holder.close()
        await heartbeat_session.close()
        await observer.close()


@pytest.mark.asyncio
async def test_waiting_match_heartbeat_loses_to_linked_revision_mutation(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
) -> None:
    lock_holder = AsyncSession(phase4a_engine)
    heartbeat_session = AsyncSession(phase4a_engine)
    observer = AsyncSession(phase4a_engine)
    try:
        await lock_holder.execute(
            text("UPDATE job_descriptions SET revision = revision WHERE id = :id"),
            {"id": heartbeat_rows.job_id},
        )
        backend_pid = await heartbeat_session.scalar(text("SELECT pg_backend_pid()"))
        assert backend_pid is not None
        renewal = asyncio.create_task(
            renew_processing_attempt_lease(
                heartbeat_session, heartbeat_rows.match_guard, lease_seconds=60
            )
        )
        await _wait_until_blocked(observer, backend_pid)
        await lock_holder.execute(
            text("UPDATE resumes SET revision = revision + 1 WHERE id = :id"),
            {"id": heartbeat_rows.resume_id},
        )
        await lock_holder.commit()
        assert await renewal is ProcessingHeartbeatOutcome.LOST_OWNERSHIP
    finally:
        await lock_holder.close()
        await heartbeat_session.close()
        await observer.close()


@pytest.mark.asyncio
async def test_disconnect_while_waiting_is_not_reported_as_renewed(
    phase4a_engine: AsyncEngine,
    heartbeat_rows: HeartbeatRows,
) -> None:
    lock_holder = AsyncSession(phase4a_engine)
    heartbeat_session = AsyncSession(phase4a_engine)
    terminator = AsyncSession(phase4a_engine)
    try:
        await lock_holder.execute(
            text("UPDATE resumes SET revision = revision WHERE id = :id"),
            {"id": heartbeat_rows.resume_id},
        )
        backend_pid = await heartbeat_session.scalar(text("SELECT pg_backend_pid()"))
        assert backend_pid is not None
        renewal = asyncio.create_task(
            renew_processing_attempt_lease(
                heartbeat_session, heartbeat_rows.resume_guard, lease_seconds=60
            )
        )
        await _wait_until_blocked(terminator, backend_pid)
        assert await terminator.scalar(
            text("SELECT pg_terminate_backend(:pid)"), {"pid": backend_pid}
        )
        assert await renewal is ProcessingHeartbeatOutcome.PERSISTENCE_ERROR
        await lock_holder.rollback()
    finally:
        await lock_holder.close()
        await heartbeat_session.close()
        await terminator.close()
