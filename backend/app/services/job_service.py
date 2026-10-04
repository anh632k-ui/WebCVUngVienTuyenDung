from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import APIError
from app.models.job import JobDescription
from app.models.skill import JobSkill
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.job_schema import JobStatus, ParsingStatus


def _visibility_filters(current_user: User) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [JobDescription.is_deleted.is_(False)]
    if current_user.role == UserRole.CANDIDATE.value:
        filters.append(JobDescription.status == JobStatus.ACTIVE.value)
    elif current_user.role == UserRole.HR.value:
        filters.append(JobDescription.recruiter_id == current_user.id)
    return filters


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_jobs(
    session: AsyncSession,
    *,
    current_user: User,
    keyword: str | None,
    status: JobStatus | None,
    parsing_status: ParsingStatus | None,
    page: int,
    limit: int,
) -> tuple[list[JobDescription], int]:
    filters = _visibility_filters(current_user)
    if keyword:
        pattern = f"%{_escape_like(keyword)}%"
        filters.append(
            or_(
                JobDescription.title.ilike(pattern, escape="\\"),
                JobDescription.location.ilike(pattern, escape="\\"),
                JobDescription.raw_content.ilike(pattern, escape="\\"),
            )
        )
    if status is not None:
        filters.append(JobDescription.status == status.value)
    if parsing_status is not None:
        filters.append(JobDescription.parsing_status == parsing_status.value)

    total_items = (
        await session.scalar(select(func.count()).select_from(JobDescription).where(*filters)) or 0
    )
    statement = (
        select(JobDescription)
        .where(*filters)
        .order_by(JobDescription.created_at.desc(), JobDescription.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list((await session.scalars(statement)).all()), total_items


async def get_job(
    session: AsyncSession,
    *,
    current_user: User,
    job_id: uuid.UUID,
) -> JobDescription:
    job = await session.scalar(
        select(JobDescription).where(
            JobDescription.id == job_id, *_visibility_filters(current_user)
        )
    )
    if job is None:
        raise APIError(404, "JOB_NOT_FOUND", "Job not found")
    return job


async def soft_delete_job(
    session: AsyncSession,
    *,
    current_user: User,
    job_id: uuid.UUID,
) -> None:
    filters: list[ColumnElement[bool]] = [
        JobDescription.id == job_id,
        JobDescription.is_deleted.is_(False),
    ]
    if current_user.role == UserRole.HR.value:
        filters.append(JobDescription.recruiter_id == current_user.id)

    job = await session.scalar(select(JobDescription).where(*filters).with_for_update())
    if job is None:
        raise APIError(404, "JOB_NOT_FOUND", "Job not found")

    now = datetime.now(UTC)
    job.is_deleted = True
    job.deleted_at = now
    job.updated_at = now
    await session.commit()


async def change_job_status(
    session: AsyncSession,
    *,
    current_user: User,
    job_id: uuid.UUID,
    target_status: JobStatus,
) -> JobDescription:
    filters: list[ColumnElement[bool]] = [
        JobDescription.id == job_id,
        JobDescription.is_deleted.is_(False),
    ]
    if current_user.role == UserRole.HR.value:
        filters.append(JobDescription.recruiter_id == current_user.id)

    job = await session.scalar(select(JobDescription).where(*filters).with_for_update())
    if job is None:
        raise APIError(404, "JOB_NOT_FOUND", "Job not found")
    if job.status == target_status.value:
        return job
    if job.status == JobStatus.DRAFT.value and target_status is JobStatus.CLOSED:
        raise APIError(
            422,
            "INVALID_STATUS_TRANSITION",
            "DRAFT jobs cannot transition directly to CLOSED",
        )

    if target_status is JobStatus.ACTIVE:
        has_skill = await session.scalar(
            select(select(JobSkill.id).where(JobSkill.job_id == job.id).exists())
        )
        ready = (
            job.parsing_status == ParsingStatus.PARSED.value
            and job.is_criteria_verified
            and job.job_embedding is not None
            and bool(job.embedding_model)
            and bool(job.embedding_preprocessing_version)
            and bool(has_skill)
        )
        if not ready:
            raise APIError(
                422,
                "JOB_NOT_READY",
                "Job does not satisfy ACTIVE readiness requirements",
            )

    job.status = target_status.value
    job.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(job)
    return job
