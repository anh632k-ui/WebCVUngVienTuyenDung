from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume

BIGINT_MAX = 2**63 - 1
SessionFactory = Callable[[], AsyncSession]


class ProcessingResourceKind(StrEnum):
    RESUME = "resume"
    JOB = "job"
    MATCH = "match"


class ProcessingFencingPersistenceError(RuntimeError):
    """Credential- and token-free persistence failure."""


@dataclass(frozen=True, slots=True)
class ProcessingAttemptGuard:
    """Immutable capability for one persisted processing attempt.

    Tokens are deliberately excluded from repr so logs and exceptions cannot expose
    the database ownership capability accidentally.
    """

    resource_kind: ProcessingResourceKind
    resource_id: uuid.UUID
    snapshot: int
    attempt_epoch: int
    owner_token: uuid.UUID = field(repr=False)
    activation_token: uuid.UUID | None = field(repr=False)
    resume_revision: int | None = None
    job_revision: int | None = None
    algorithm_version: str | None = None


def _clock() -> Any:
    # Unlike transaction_timestamp()/now(), this advances while a statement waits
    # for a row lock, so an expired lease cannot regain validity after lock wait.
    return func.clock_timestamp()


def _lease(lease_seconds: int) -> Any:
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or not 60 <= lease_seconds <= 86_400
    ):
        raise ValueError("Processing fencing lease must be between 60 and 86400 seconds")
    return _clock() + timedelta(seconds=lease_seconds)


def _cleared_ownership() -> dict[str, Any]:
    return {
        "processing_owner_token": None,
        "processing_activation_token": None,
        "processing_heartbeat_at": None,
        "processing_lease_expires_at": None,
    }


def unowned_processing(model: Any) -> tuple[Any, ...]:
    """Match rows with no active persisted fencing capability, at any epoch."""

    return (
        model.processing_owner_token.is_(None),
        model.processing_activation_token.is_(None),
        model.processing_heartbeat_at.is_(None),
        model.processing_lease_expires_at.is_(None),
    )


def clear_processing_ownership(resource: Any) -> None:
    """Invalidate an in-flight capability during a locked business mutation."""

    for field_name in _cleared_ownership():
        setattr(resource, field_name, None)


def terminal_ownership_values() -> dict[str, Any]:
    """Values every fenced terminal transition must apply."""

    return _cleared_ownership()


async def _returning_cas(session: AsyncSession, statement: Any) -> Any | None:
    try:
        value = (await session.execute(statement)).scalar_one_or_none()
        if value is None:
            await session.rollback()
        else:
            await session.commit()
        return value
    except Exception:
        with suppress(Exception):
            await session.rollback()
        raise ProcessingFencingPersistenceError(
            "Processing fencing persistence operation failed"
        ) from None


def resume_fence(guard: ProcessingAttemptGuard) -> tuple[Any, ...]:
    if guard.resource_kind is not ProcessingResourceKind.RESUME or guard.activation_token is None:
        raise ValueError("A fully activated Resume attempt guard is required")
    return (
        Resume.id == guard.resource_id,
        Resume.revision == guard.snapshot,
        Resume.parsing_status == "PROCESSING",
        Resume.is_deleted.is_(False),
        Resume.processing_attempt_epoch == guard.attempt_epoch,
        Resume.processing_owner_token == guard.owner_token,
        Resume.processing_activation_token == guard.activation_token,
        Resume.processing_lease_expires_at > _clock(),
    )


def job_fence(guard: ProcessingAttemptGuard) -> tuple[Any, ...]:
    if guard.resource_kind is not ProcessingResourceKind.JOB or guard.activation_token is None:
        raise ValueError("A fully activated Job attempt guard is required")
    return (
        JobDescription.id == guard.resource_id,
        JobDescription.revision == guard.snapshot,
        JobDescription.parsing_status == "PROCESSING",
        JobDescription.is_deleted.is_(False),
        JobDescription.processing_attempt_epoch == guard.attempt_epoch,
        JobDescription.processing_owner_token == guard.owner_token,
        JobDescription.processing_activation_token == guard.activation_token,
        JobDescription.processing_lease_expires_at > _clock(),
    )


def match_fence(guard: ProcessingAttemptGuard) -> tuple[Any, ...]:
    if guard.resource_kind is not ProcessingResourceKind.MATCH or guard.activation_token is None:
        raise ValueError("A fully activated Match attempt guard is required")
    if (
        guard.resume_revision is None
        or guard.job_revision is None
        or guard.algorithm_version is None
    ):
        raise ValueError("A complete Match attempt guard is required")
    return (
        MatchResult.id == guard.resource_id,
        MatchResult.generation == guard.snapshot,
        MatchResult.resume_revision == guard.resume_revision,
        MatchResult.job_revision == guard.job_revision,
        MatchResult.algorithm_version == guard.algorithm_version,
        MatchResult.status == "PROCESSING",
        MatchResult.processing_attempt_epoch == guard.attempt_epoch,
        MatchResult.processing_owner_token == guard.owner_token,
        MatchResult.processing_activation_token == guard.activation_token,
        MatchResult.processing_lease_expires_at > _clock(),
    )


async def claim_resume_attempt(
    session: AsyncSession,
    *,
    resume_id: uuid.UUID,
    expected_revision: int,
    lease_seconds: int,
) -> ProcessingAttemptGuard | None:
    owner_token, activation_token = uuid.uuid4(), uuid.uuid4()
    statement = (
        update(Resume)
        .where(
            Resume.id == resume_id,
            Resume.revision == expected_revision,
            Resume.parsing_status == "PENDING",
            Resume.is_deleted.is_(False),
            Resume.processing_attempt_epoch < BIGINT_MAX,
        )
        .values(
            parsing_status="PROCESSING",
            error_message=None,
            processing_attempt_epoch=Resume.processing_attempt_epoch + 1,
            processing_owner_token=owner_token,
            processing_activation_token=activation_token,
            processing_heartbeat_at=_clock(),
            processing_lease_expires_at=_lease(lease_seconds),
            updated_at=_clock(),
        )
        .returning(Resume.processing_attempt_epoch)
    )
    epoch = await _returning_cas(session, statement)
    if epoch is None:
        return None
    return ProcessingAttemptGuard(
        ProcessingResourceKind.RESUME,
        resume_id,
        expected_revision,
        epoch,
        owner_token,
        activation_token,
    )


async def claim_job_attempt(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    expected_revision: int,
    lease_seconds: int,
) -> ProcessingAttemptGuard | None:
    owner_token, activation_token = uuid.uuid4(), uuid.uuid4()
    statement = (
        update(JobDescription)
        .where(
            JobDescription.id == job_id,
            JobDescription.revision == expected_revision,
            JobDescription.parsing_status == "PENDING",
            JobDescription.is_deleted.is_(False),
            JobDescription.processing_attempt_epoch < BIGINT_MAX,
        )
        .values(
            parsing_status="PROCESSING",
            parsing_error_message=None,
            processing_attempt_epoch=JobDescription.processing_attempt_epoch + 1,
            processing_owner_token=owner_token,
            processing_activation_token=activation_token,
            processing_heartbeat_at=_clock(),
            processing_lease_expires_at=_lease(lease_seconds),
            updated_at=_clock(),
        )
        .returning(JobDescription.processing_attempt_epoch)
    )
    epoch = await _returning_cas(session, statement)
    if epoch is None:
        return None
    return ProcessingAttemptGuard(
        ProcessingResourceKind.JOB,
        job_id,
        expected_revision,
        epoch,
        owner_token,
        activation_token,
    )


async def claim_match_attempt(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    expected_generation: int,
    expected_resume_revision: int,
    expected_job_revision: int,
    algorithm_version: str,
    lease_seconds: int,
) -> ProcessingAttemptGuard | None:
    owner_token, activation_token = uuid.uuid4(), uuid.uuid4()
    resume_is_current = exists(
        select(Resume.id).where(
            Resume.id == MatchResult.resume_id,
            Resume.revision == expected_resume_revision,
            Resume.is_deleted.is_(False),
        )
    )
    job_is_current = exists(
        select(JobDescription.id).where(
            JobDescription.id == MatchResult.job_id,
            JobDescription.revision == expected_job_revision,
            JobDescription.is_deleted.is_(False),
        )
    )
    statement = (
        update(MatchResult)
        .where(
            MatchResult.id == match_id,
            MatchResult.generation == expected_generation,
            MatchResult.resume_revision == expected_resume_revision,
            MatchResult.job_revision == expected_job_revision,
            MatchResult.algorithm_version == algorithm_version,
            MatchResult.status == "PENDING",
            MatchResult.processing_attempt_epoch < BIGINT_MAX,
            resume_is_current,
            job_is_current,
        )
        .values(
            status="PROCESSING",
            error_message=None,
            processing_attempt_epoch=MatchResult.processing_attempt_epoch + 1,
            processing_owner_token=owner_token,
            processing_activation_token=activation_token,
            processing_heartbeat_at=_clock(),
            processing_lease_expires_at=_lease(lease_seconds),
            updated_at=_clock(),
        )
        .returning(MatchResult.processing_attempt_epoch)
    )
    epoch = await _returning_cas(session, statement)
    if epoch is None:
        return None
    return ProcessingAttemptGuard(
        ProcessingResourceKind.MATCH,
        match_id,
        expected_generation,
        epoch,
        owner_token,
        activation_token,
        resume_revision=expected_resume_revision,
        job_revision=expected_job_revision,
        algorithm_version=algorithm_version,
    )


async def _activate(
    session: AsyncSession,
    guard: ProcessingAttemptGuard,
    *,
    lease_seconds: int,
) -> ProcessingAttemptGuard | None:
    if guard.activation_token is not None:
        raise ValueError("Reclaimed attempt guard must not already be activated")
    activation_token = uuid.uuid4()
    common_values = {
        "processing_activation_token": activation_token,
        "processing_heartbeat_at": _clock(),
        "processing_lease_expires_at": _lease(lease_seconds),
        "updated_at": _clock(),
    }
    if guard.resource_kind is ProcessingResourceKind.RESUME:
        statement = (
            update(Resume)
            .where(
                Resume.id == guard.resource_id,
                Resume.revision == guard.snapshot,
                Resume.parsing_status == "PROCESSING",
                Resume.is_deleted.is_(False),
                Resume.processing_attempt_epoch == guard.attempt_epoch,
                Resume.processing_owner_token == guard.owner_token,
                Resume.processing_activation_token.is_(None),
                Resume.processing_lease_expires_at > _clock(),
            )
            .values(**common_values)
            .returning(Resume.id)
        )
    elif guard.resource_kind is ProcessingResourceKind.JOB:
        statement = (
            update(JobDescription)
            .where(
                JobDescription.id == guard.resource_id,
                JobDescription.revision == guard.snapshot,
                JobDescription.parsing_status == "PROCESSING",
                JobDescription.is_deleted.is_(False),
                JobDescription.processing_attempt_epoch == guard.attempt_epoch,
                JobDescription.processing_owner_token == guard.owner_token,
                JobDescription.processing_activation_token.is_(None),
                JobDescription.processing_lease_expires_at > _clock(),
            )
            .values(**common_values)
            .returning(JobDescription.id)
        )
    else:
        if (
            guard.resume_revision is None
            or guard.job_revision is None
            or guard.algorithm_version is None
        ):
            raise ValueError("A complete Match attempt guard is required")
        resume_is_current = exists(
            select(Resume.id).where(
                Resume.id == MatchResult.resume_id,
                Resume.revision == guard.resume_revision,
                Resume.is_deleted.is_(False),
            )
        )
        job_is_current = exists(
            select(JobDescription.id).where(
                JobDescription.id == MatchResult.job_id,
                JobDescription.revision == guard.job_revision,
                JobDescription.is_deleted.is_(False),
            )
        )
        statement = (
            update(MatchResult)
            .where(
                MatchResult.id == guard.resource_id,
                MatchResult.generation == guard.snapshot,
                MatchResult.resume_revision == guard.resume_revision,
                MatchResult.job_revision == guard.job_revision,
                MatchResult.algorithm_version == guard.algorithm_version,
                MatchResult.status == "PROCESSING",
                MatchResult.processing_attempt_epoch == guard.attempt_epoch,
                MatchResult.processing_owner_token == guard.owner_token,
                MatchResult.processing_activation_token.is_(None),
                MatchResult.processing_lease_expires_at > _clock(),
                resume_is_current,
                job_is_current,
            )
            .values(**common_values)
            .returning(MatchResult.id)
        )
    won = await _returning_cas(session, statement)
    if won is None:
        return None
    return replace(guard, activation_token=activation_token)


async def activate_reclaimed_attempt(
    session: AsyncSession,
    guard: ProcessingAttemptGuard,
    *,
    lease_seconds: int,
) -> ProcessingAttemptGuard | None:
    """Activate one already-persisted reclaimed attempt; it never performs reclaim."""

    return await _activate(session, guard, lease_seconds=lease_seconds)


class ProcessingFencingSchemaNotReady(RuntimeError):
    """Safe deployment error; details contain no database credentials."""


_READINESS_SQL = text(
    """
    WITH required_columns(table_name, column_name, data_type, is_nullable) AS (
      VALUES
        ('resumes', 'processing_attempt_epoch', 'bigint', 'NO'),
        ('resumes', 'processing_owner_token', 'uuid', 'YES'),
        ('resumes', 'processing_activation_token', 'uuid', 'YES'),
        ('resumes', 'processing_heartbeat_at', 'timestamp with time zone', 'YES'),
        ('resumes', 'processing_lease_expires_at', 'timestamp with time zone', 'YES'),
        ('job_descriptions', 'processing_attempt_epoch', 'bigint', 'NO'),
        ('job_descriptions', 'processing_owner_token', 'uuid', 'YES'),
        ('job_descriptions', 'processing_activation_token', 'uuid', 'YES'),
        ('job_descriptions', 'processing_heartbeat_at', 'timestamp with time zone', 'YES'),
        ('job_descriptions', 'processing_lease_expires_at', 'timestamp with time zone', 'YES'),
        ('match_results', 'processing_attempt_epoch', 'bigint', 'NO'),
        ('match_results', 'processing_owner_token', 'uuid', 'YES'),
        ('match_results', 'processing_activation_token', 'uuid', 'YES'),
        ('match_results', 'processing_heartbeat_at', 'timestamp with time zone', 'YES'),
        ('match_results', 'processing_lease_expires_at', 'timestamp with time zone', 'YES')
    ), required_indexes(indexname, table_name, status_column) AS (
      VALUES ('idx_resumes_processing_lease', 'resumes', 'parsing_status'),
             ('idx_jobs_processing_lease', 'job_descriptions', 'parsing_status'),
             ('idx_matches_processing_lease', 'match_results', 'status')
    )
    SELECT
      (SELECT count(*) FROM required_columns rc
       JOIN information_schema.columns c
         ON c.table_schema = current_schema()
        AND c.table_name = rc.table_name AND c.column_name = rc.column_name
        AND c.data_type = rc.data_type AND c.is_nullable = rc.is_nullable
        AND (rc.column_name <> 'processing_attempt_epoch'
             OR c.column_default IN ('0', '0::bigint'))),
      (SELECT count(*) FROM required_indexes ri
       JOIN pg_namespace idx_ns
         ON idx_ns.nspname = current_schema()
       JOIN pg_class idx
         ON idx.relnamespace = idx_ns.oid
        AND idx.relname = ri.indexname
        AND idx.relkind = 'i'
       JOIN pg_index i
         ON i.indexrelid = idx.oid
        AND i.indisvalid
        AND i.indisready
        AND NOT i.indisunique
        AND i.indnkeyatts = 2
        AND i.indnatts = 2
       JOIN pg_class tbl
         ON tbl.oid = i.indrelid
        AND tbl.relname = ri.table_name
       JOIN pg_namespace tbl_ns
         ON tbl_ns.oid = tbl.relnamespace
        AND tbl_ns.nspname = current_schema()
       JOIN pg_am access_method
         ON access_method.oid = idx.relam
        AND access_method.amname = 'btree'
       WHERE ARRAY(
               SELECT att.attname::text
               FROM unnest(i.indkey) WITH ORDINALITY AS key(attnum, ordinality)
               JOIN pg_attribute att
                 ON att.attrelid = tbl.oid
                AND att.attnum = key.attnum
               ORDER BY key.ordinality
             ) = ARRAY['processing_lease_expires_at', 'id']::text[]
         AND regexp_replace(
               replace(
                 replace(
                   replace(pg_get_expr(i.indpred, i.indrelid), '::text', ''),
                   '(', ''
                 ),
                 ')', ''
               ),
               '[[:space:]]', '', 'g'
             ) IS NOT DISTINCT FROM
             ri.status_column || '=''PROCESSING''ANDprocessing_lease_expires_atISNOTNULL')
    """
)


async def verify_processing_fencing_schema(session_factory: SessionFactory) -> None:
    """Fail closed when the Phase 1 expansion is absent; never performs DDL."""

    try:
        async with session_factory() as session:
            row = (await session.execute(_READINESS_SQL)).one()
    except Exception:
        raise ProcessingFencingSchemaNotReady(
            "Processing fencing schema readiness could not be verified"
        ) from None
    if tuple(row) != (15, 3):
        raise ProcessingFencingSchemaNotReady(
            "Processing fencing schema is not ready (required columns or indexes are missing)"
        )
