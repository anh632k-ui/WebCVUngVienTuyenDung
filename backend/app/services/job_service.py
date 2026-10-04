from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIError
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import CandidateProfile, Resume
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.match_schema import MatchStatus


async def list_job_leaderboard(
    session: AsyncSession,
    *,
    current_user: User,
    job_id: uuid.UUID,
    page: int,
    limit: int,
    min_score: Decimal | None,
) -> tuple[list[tuple[MatchResult, CandidateProfile | None]], int]:
    job_filters = [
        JobDescription.id == job_id,
        JobDescription.is_deleted.is_(False),
    ]
    if current_user.role == UserRole.HR.value:
        job_filters.append(JobDescription.recruiter_id == current_user.id)

    job = await session.scalar(select(JobDescription.id).where(*job_filters))
    if job is None:
        raise APIError(404, "JOB_NOT_FOUND", "Job not found")

    statement = (
        select(MatchResult, CandidateProfile)
        .join(Resume, Resume.id == MatchResult.resume_id)
        .join(JobDescription, JobDescription.id == MatchResult.job_id)
        .outerjoin(CandidateProfile, CandidateProfile.resume_id == Resume.id)
        .where(
            MatchResult.job_id == job_id,
            MatchResult.status == MatchStatus.COMPLETED.value,
            Resume.is_deleted.is_(False),
            JobDescription.is_deleted.is_(False),
        )
    )
    if current_user.role == UserRole.HR.value:
        statement = statement.where(Resume.owner_user_id == current_user.id)
    if min_score is not None:
        statement = statement.where(MatchResult.overall_score >= min_score)

    total_items = (
        await session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
        or 0
    )
    statement = (
        statement.order_by(MatchResult.overall_score.desc(), MatchResult.id.asc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    return [(match, profile) for match, profile in rows], total_items
