from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.core.exceptions import APIError
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.match_schema import MatchStatus


def _visible_matches(current_user: User) -> Select[MatchResult]:
    statement = (
        select(MatchResult)
        .join(Resume, Resume.id == MatchResult.resume_id)
        .join(JobDescription, JobDescription.id == MatchResult.job_id)
        .where(Resume.is_deleted.is_(False), JobDescription.is_deleted.is_(False))
    )
    if current_user.role == UserRole.CANDIDATE.value:
        statement = statement.where(Resume.owner_user_id == current_user.id)
    elif current_user.role == UserRole.HR.value:
        statement = statement.where(
            Resume.owner_user_id == current_user.id,
            JobDescription.recruiter_id == current_user.id,
        )
    return statement


async def list_matches(
    session: AsyncSession,
    *,
    current_user: User,
    job_id: uuid.UUID | None,
    resume_id: uuid.UUID | None,
    status: MatchStatus | None,
    page: int,
    limit: int,
) -> tuple[list[MatchResult], int]:
    statement = _visible_matches(current_user)
    if job_id is not None:
        statement = statement.where(MatchResult.job_id == job_id)
    if resume_id is not None:
        statement = statement.where(MatchResult.resume_id == resume_id)
    if status is not None:
        statement = statement.where(MatchResult.status == status.value)

    total_items = (
        await session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
        or 0
    )
    statement = (
        statement.order_by(MatchResult.created_at.desc(), MatchResult.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list((await session.scalars(statement)).all()), total_items


async def get_match(
    session: AsyncSession,
    *,
    current_user: User,
    match_id: uuid.UUID,
) -> MatchResult:
    match = await session.scalar(_visible_matches(current_user).where(MatchResult.id == match_id))
    if match is None:
        raise APIError(404, "MATCH_NOT_FOUND", "Match not found")
    return match


async def get_gap_analysis(
    session: AsyncSession,
    *,
    current_user: User,
    match_id: uuid.UUID,
) -> MatchResult:
    match = await get_match(session, current_user=current_user, match_id=match_id)
    if match.status != MatchStatus.COMPLETED.value:
        raise APIError(
            422,
            "MATCH_NOT_COMPLETED",
            "Gap analysis is available only for COMPLETED matches",
        )
    return match
