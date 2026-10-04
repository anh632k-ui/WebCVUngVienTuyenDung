from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import APIError
from app.models.resume import CandidateProfile, Resume, ResumeEducation, ResumeExperience
from app.models.skill import ResumeSkill
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.resume_schema import ParsingStatus


@dataclass
class ResumeAggregate:
    resume: Resume
    candidate_profile: CandidateProfile | None
    skills: list[ResumeSkill]
    experiences: list[ResumeExperience]
    educations: list[ResumeEducation]


def _visibility_filters(current_user: User) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [Resume.is_deleted.is_(False)]
    if current_user.role != UserRole.ADMIN.value:
        filters.append(Resume.owner_user_id == current_user.id)
    return filters


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_resumes(
    session: AsyncSession,
    *,
    current_user: User,
    keyword: str | None,
    parsing_status: ParsingStatus | None,
    page: int,
    limit: int,
) -> tuple[list[Resume], int]:
    filters = _visibility_filters(current_user)
    if keyword:
        filters.append(Resume.file_name.ilike(f"%{_escape_like(keyword)}%", escape="\\"))
    if parsing_status is not None:
        filters.append(Resume.parsing_status == parsing_status.value)

    total_items = (
        await session.scalar(select(func.count()).select_from(Resume).where(*filters)) or 0
    )
    statement = (
        select(Resume)
        .where(*filters)
        .order_by(Resume.created_at.desc(), Resume.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list((await session.scalars(statement)).all()), total_items


async def get_resume(
    session: AsyncSession,
    *,
    current_user: User,
    resume_id: uuid.UUID,
) -> Resume:
    resume = await session.scalar(
        select(Resume).where(Resume.id == resume_id, *_visibility_filters(current_user))
    )
    if resume is None:
        raise APIError(404, "RESUME_NOT_FOUND", "Resume not found")
    return resume


async def get_resume_aggregate(
    session: AsyncSession,
    *,
    current_user: User,
    resume_id: uuid.UUID,
) -> ResumeAggregate:
    resume = await get_resume(session, current_user=current_user, resume_id=resume_id)
    profile = await session.scalar(
        select(CandidateProfile).where(CandidateProfile.resume_id == resume.id)
    )
    skills = list(
        (
            await session.scalars(
                select(ResumeSkill)
                .where(ResumeSkill.resume_id == resume.id)
                .order_by(ResumeSkill.id.asc())
            )
        ).all()
    )
    experiences = list(
        (
            await session.scalars(
                select(ResumeExperience)
                .where(ResumeExperience.resume_id == resume.id)
                .order_by(ResumeExperience.id.asc())
            )
        ).all()
    )
    educations = list(
        (
            await session.scalars(
                select(ResumeEducation)
                .where(ResumeEducation.resume_id == resume.id)
                .order_by(ResumeEducation.id.asc())
            )
        ).all()
    )
    return ResumeAggregate(resume, profile, skills, experiences, educations)
