from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]
ResourceKind = Literal["resume", "job", "match"]


@dataclass(frozen=True)
class ProcessingObservation:
    """Non-sensitive identity of old PROCESSING work; it is not proof of abandonment."""

    resource_kind: ResourceKind
    resource_id: uuid.UUID
    attempt_version: int
    updated_at: datetime
    is_deleted: bool | None = None
    resume_revision: int | None = None
    job_revision: int | None = None


@dataclass(frozen=True)
class ProcessingObservationReport:
    enabled: bool
    cutoff: datetime | None
    candidates: tuple[ProcessingObservation, ...]


async def select_old_processing(
    session_factory: SessionFactory,
    *,
    cutoff: datetime,
    batch_size: int,
) -> tuple[ProcessingObservation, ...]:
    """Read bounded PROCESSING metadata without claiming, failing, or dispatching work."""

    async with session_factory() as session:
        resumes = (
            await session.execute(
                select(Resume.id, Resume.revision, Resume.updated_at, Resume.is_deleted)
                .where(Resume.parsing_status == "PROCESSING", Resume.updated_at <= cutoff)
                .order_by(Resume.updated_at.asc(), Resume.id.asc())
                .limit(batch_size)
            )
        ).all()
        jobs = (
            await session.execute(
                select(
                    JobDescription.id,
                    JobDescription.revision,
                    JobDescription.updated_at,
                    JobDescription.is_deleted,
                )
                .where(
                    JobDescription.parsing_status == "PROCESSING",
                    JobDescription.updated_at <= cutoff,
                )
                .order_by(JobDescription.updated_at.asc(), JobDescription.id.asc())
                .limit(batch_size)
            )
        ).all()
        matches = (
            await session.execute(
                select(
                    MatchResult.id,
                    MatchResult.generation,
                    MatchResult.updated_at,
                    MatchResult.resume_revision,
                    MatchResult.job_revision,
                )
                .where(MatchResult.status == "PROCESSING", MatchResult.updated_at <= cutoff)
                .order_by(MatchResult.updated_at.asc(), MatchResult.id.asc())
                .limit(batch_size)
            )
        ).all()

    candidates = [
        *(
            ProcessingObservation("resume", row.id, row.revision, row.updated_at, row.is_deleted)
            for row in resumes
        ),
        *(
            ProcessingObservation("job", row.id, row.revision, row.updated_at, row.is_deleted)
            for row in jobs
        ),
        *(
            ProcessingObservation(
                "match",
                row.id,
                row.generation,
                row.updated_at,
                resume_revision=row.resume_revision,
                job_revision=row.job_revision,
            )
            for row in matches
        ),
    ]
    candidates.sort(key=lambda item: (item.updated_at, item.resource_kind, item.resource_id))
    return tuple(candidates[:batch_size])


async def observe_old_processing(
    settings: Settings,
    session_factory: SessionFactory,
    *,
    now: datetime | None = None,
) -> ProcessingObservationReport:
    """Collect an opt-in snapshot and log counts only; never infer worker death."""

    if not settings.processing_observation_enabled:
        return ProcessingObservationReport(enabled=False, cutoff=None, candidates=())

    cutoff = (now or datetime.now(UTC)) - timedelta(
        seconds=settings.processing_observation_threshold_seconds
    )
    candidates = await select_old_processing(
        session_factory,
        cutoff=cutoff,
        batch_size=settings.processing_observation_batch_size,
    )
    counts = {
        kind: sum(item.resource_kind == kind for item in candidates)
        for kind in ("resume", "job", "match")
    }
    logger.warning(
        "old_processing_observed total=%s resumes=%s jobs=%s matches=%s",
        len(candidates),
        counts["resume"],
        counts["job"],
        counts["match"],
    )
    return ProcessingObservationReport(enabled=True, cutoff=cutoff, candidates=candidates)
