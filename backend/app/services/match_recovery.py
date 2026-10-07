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

from app.ai.matching_engine import ALGORITHM_VERSION
from app.core.config import Settings
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume
from app.services.match_dispatcher import MatchDispatcher, build_match_dispatcher

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class MatchRecoveryCandidate:
    match_id: uuid.UUID
    expected_generation: int
    expected_resume_revision: int
    expected_job_revision: int
    algorithm_version: str


@dataclass(frozen=True)
class MatchRecoveryResult:
    selected: int
    dispatched: int
    failed: int


async def select_match_recovery_candidates(
    session_factory: SessionFactory,
    *,
    cutoff: datetime,
    batch_size: int,
) -> tuple[MatchRecoveryCandidate, ...]:
    """Read current, unclaimed Match snapshots; never hold a session over publication."""

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    MatchResult.id,
                    MatchResult.generation,
                    MatchResult.resume_revision,
                    MatchResult.job_revision,
                    MatchResult.algorithm_version,
                )
                .join(Resume, Resume.id == MatchResult.resume_id)
                .join(JobDescription, JobDescription.id == MatchResult.job_id)
                .where(
                    MatchResult.status == "PENDING",
                    MatchResult.updated_at <= cutoff,
                    MatchResult.algorithm_version == ALGORITHM_VERSION,
                    MatchResult.resume_revision == Resume.revision,
                    MatchResult.job_revision == JobDescription.revision,
                    Resume.is_deleted.is_(False),
                    JobDescription.is_deleted.is_(False),
                )
                .order_by(MatchResult.updated_at.asc(), MatchResult.id.asc())
                .limit(batch_size)
            )
        ).all()
    return tuple(
        MatchRecoveryCandidate(
            row.id,
            row.generation,
            row.resume_revision,
            row.job_revision,
            row.algorithm_version,
        )
        for row in rows
    )


async def recover_pending_matches(
    session_factory: SessionFactory,
    dispatcher: MatchDispatcher,
    *,
    grace_seconds: int,
    batch_size: int,
    now: datetime | None = None,
) -> MatchRecoveryResult:
    candidates = await select_match_recovery_candidates(
        session_factory,
        cutoff=(now or datetime.now(UTC)) - timedelta(seconds=grace_seconds),
        batch_size=batch_size,
    )
    dispatched = 0
    failed = 0
    for candidate in candidates:
        try:
            await dispatcher.dispatch(
                candidate.match_id,
                candidate.expected_generation,
                candidate.expected_resume_revision,
                candidate.expected_job_revision,
                candidate.algorithm_version,
            )
            dispatched += 1
        except Exception:  # noqa: BLE001 - one broker failure must not stop the batch
            failed += 1
            logger.error(
                "match_recovery_dispatch_failed match_id=%s generation=%s",
                candidate.match_id,
                candidate.expected_generation,
            )
    result = MatchRecoveryResult(len(candidates), dispatched, failed)
    logger.info(
        "match_recovery_sweep selected=%s dispatched=%s failed=%s",
        result.selected,
        result.dispatched,
        result.failed,
    )
    return result


async def run_match_recovery_loop(
    session_factory: SessionFactory,
    dispatcher: MatchDispatcher,
    *,
    grace_seconds: int,
    interval_seconds: int,
    batch_size: int,
    sleep: Sleep = asyncio.sleep,
) -> None:
    while True:
        try:
            await recover_pending_matches(
                session_factory,
                dispatcher,
                grace_seconds=grace_seconds,
                batch_size=batch_size,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - transient DB/queue failures retry next interval
            logger.error("match_recovery_sweep_failed")
        await sleep(interval_seconds)


def start_match_recovery(
    settings: Settings,
    session_factory: SessionFactory | None,
) -> asyncio.Task[None] | None:
    if settings.celery_broker_url is None or session_factory is None:
        return None
    return asyncio.create_task(
        run_match_recovery_loop(
            session_factory,
            build_match_dispatcher(settings),
            grace_seconds=settings.match_recovery_grace_seconds,
            interval_seconds=settings.match_recovery_interval_seconds,
            batch_size=settings.match_recovery_batch_size,
        ),
        name="match-pending-recovery",
    )


async def stop_match_recovery(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
