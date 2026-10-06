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
from app.models.resume import Resume
from app.services.resume_dispatcher import ResumeParseDispatcher, build_resume_parse_dispatcher

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class ResumeRecoveryCandidate:
    resume_id: uuid.UUID
    revision: int


@dataclass(frozen=True)
class ResumeRecoveryResult:
    selected: int
    dispatched: int
    failed: int


async def select_resume_recovery_candidates(
    session_factory: SessionFactory,
    *,
    cutoff: datetime,
    batch_size: int,
) -> tuple[ResumeRecoveryCandidate, ...]:
    """Materialize stale PENDING work in a short, read-only database session."""

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Resume.id, Resume.revision)
                .where(
                    Resume.parsing_status == "PENDING",
                    Resume.is_deleted.is_(False),
                    Resume.updated_at <= cutoff,
                )
                .order_by(Resume.updated_at.asc(), Resume.id.asc())
                .limit(batch_size)
            )
        ).all()
    return tuple(ResumeRecoveryCandidate(row.id, row.revision) for row in rows)


async def recover_pending_resumes(
    session_factory: SessionFactory,
    dispatcher: ResumeParseDispatcher,
    *,
    grace_seconds: int,
    batch_size: int,
    now: datetime | None = None,
) -> ResumeRecoveryResult:
    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=grace_seconds)
    candidates = await select_resume_recovery_candidates(
        session_factory,
        cutoff=cutoff,
        batch_size=batch_size,
    )
    dispatched = 0
    failed = 0
    for candidate in candidates:
        try:
            await dispatcher.dispatch(candidate.resume_id, candidate.revision)
            dispatched += 1
        except Exception:  # noqa: BLE001 - one broker failure must not stop the batch
            failed += 1
            logger.error(
                "resume_recovery_dispatch_failed resume_id=%s revision=%s",
                candidate.resume_id,
                candidate.revision,
            )
    result = ResumeRecoveryResult(len(candidates), dispatched, failed)
    logger.info(
        "resume_recovery_sweep selected=%s dispatched=%s failed=%s",
        result.selected,
        result.dispatched,
        result.failed,
    )
    return result


async def run_resume_recovery_loop(
    session_factory: SessionFactory,
    dispatcher: ResumeParseDispatcher,
    *,
    grace_seconds: int,
    interval_seconds: int,
    batch_size: int,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Run an immediate sweep, then continue despite individual sweep failures."""

    while True:
        try:
            await recover_pending_resumes(
                session_factory,
                dispatcher,
                grace_seconds=grace_seconds,
                batch_size=batch_size,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - transient DB/queue failures are retried next interval
            logger.error("resume_recovery_sweep_failed")
        await sleep(interval_seconds)


def start_resume_recovery(
    settings: Settings,
    session_factory: SessionFactory | None,
) -> asyncio.Task[None] | None:
    if settings.celery_broker_url is None or session_factory is None:
        return None
    dispatcher = build_resume_parse_dispatcher(settings)
    return asyncio.create_task(
        run_resume_recovery_loop(
            session_factory,
            dispatcher,
            grace_seconds=settings.resume_recovery_grace_seconds,
            interval_seconds=settings.resume_recovery_interval_seconds,
            batch_size=settings.resume_recovery_batch_size,
        ),
        name="resume-pending-recovery",
    )


async def stop_resume_recovery(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
