from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.job import JobDescription
from app.services.job_dispatcher import JobParseDispatcher, build_job_parse_dispatcher

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class JobRecoveryCandidate:
    job_id: uuid.UUID
    revision: int


@dataclass(frozen=True)
class JobRecoveryResult:
    selected: int
    dispatched: int
    failed: int


async def select_job_recovery_candidates(
    session_factory: SessionFactory,
    *,
    cutoff: datetime,
    batch_size: int,
) -> tuple[JobRecoveryCandidate, ...]:
    """Materialize stale PENDING work in a short, read-only database session."""

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(JobDescription.id, JobDescription.revision)
                .where(
                    JobDescription.parsing_status == "PENDING",
                    JobDescription.is_deleted.is_(False),
                    JobDescription.updated_at <= cutoff,
                )
                .order_by(JobDescription.updated_at.asc(), JobDescription.id.asc())
                .limit(batch_size)
            )
        ).all()
    return tuple(JobRecoveryCandidate(row.id, row.revision) for row in rows)


async def recover_pending_jobs(
    session_factory: SessionFactory,
    dispatcher: JobParseDispatcher,
    *,
    grace_seconds: int,
    batch_size: int,
    now: datetime | None = None,
) -> JobRecoveryResult:
    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=grace_seconds)
    candidates = await select_job_recovery_candidates(
        session_factory,
        cutoff=cutoff,
        batch_size=batch_size,
    )
    dispatched = 0
    failed = 0
    for candidate in candidates:
        try:
            await dispatcher.dispatch(candidate.job_id, candidate.revision)
            dispatched += 1
        except Exception:  # noqa: BLE001 - one broker failure must not stop the batch
            failed += 1
            logger.error(
                "job_recovery_dispatch_failed job_id=%s revision=%s",
                candidate.job_id,
                candidate.revision,
            )
    result = JobRecoveryResult(len(candidates), dispatched, failed)
    logger.info(
        "job_recovery_sweep selected=%s dispatched=%s failed=%s",
        result.selected,
        result.dispatched,
        result.failed,
    )
    return result


async def run_job_recovery_loop(
    session_factory: SessionFactory,
    dispatcher: JobParseDispatcher,
    *,
    grace_seconds: int,
    interval_seconds: int,
    batch_size: int,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Run an immediate sweep, then continue despite individual sweep failures."""

    while True:
        try:
            await recover_pending_jobs(
                session_factory,
                dispatcher,
                grace_seconds=grace_seconds,
                batch_size=batch_size,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - transient DB/queue failures retry next interval
            logger.error("job_recovery_sweep_failed")
        await sleep(interval_seconds)


def start_job_recovery(
    settings: Settings,
    session_factory: SessionFactory | None,
) -> asyncio.Task[None] | None:
    if settings.celery_broker_url is None or session_factory is None:
        return None
    dispatcher = build_job_parse_dispatcher(settings)
    return asyncio.create_task(
        run_job_recovery_loop(
            session_factory,
            dispatcher,
            grace_seconds=settings.job_recovery_grace_seconds,
            interval_seconds=settings.job_recovery_interval_seconds,
            batch_size=settings.job_recovery_batch_size,
        ),
        name="job-pending-recovery",
    )


async def stop_job_recovery(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
