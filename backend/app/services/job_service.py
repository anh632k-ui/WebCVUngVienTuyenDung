from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import APIError
from app.core.idempotency import JOB_CREATE_ROUTE, derive_idempotent_resource_id
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import CandidateProfile, Resume
from app.models.skill import JobSkill, Skill
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.job_schema import (
    JobCreateRequest,
    JobCriteriaRequest,
    JobStatus,
    JobUpdateRequest,
    ParsingStatus,
)
from app.schemas.match_schema import MatchStatus
from app.services.job_create_payload import job_create_fingerprint
from app.services.job_dispatcher import JobParseDispatcher

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobCriteriaRecord:
    job: JobDescription
    skills: tuple[JobSkill, ...]


def _idempotency_conflict() -> APIError:
    return APIError(
        409,
        "IDEMPOTENCY_KEY_REUSED",
        "Idempotency-Key was reused with different Job content",
    )


async def _dispatch_if_pending(
    dispatcher: JobParseDispatcher,
    job: JobDescription,
) -> None:
    if job.parsing_status != ParsingStatus.PENDING.value:
        return
    try:
        await dispatcher.dispatch(job.id, job.revision)
    except Exception:  # noqa: BLE001 - committed Job remains independently recoverable
        logger.error(
            "job_parse_dispatch_failed job_id=%s revision=%s",
            job.id,
            job.revision,
        )


async def create_job(
    session: AsyncSession,
    *,
    current_user: User,
    idempotency_key: uuid.UUID,
    payload: JobCreateRequest,
    dispatcher: JobParseDispatcher,
) -> JobDescription:
    fingerprint = job_create_fingerprint(payload)
    job_id = derive_idempotent_resource_id(
        current_user.id,
        JOB_CREATE_ROUTE,
        idempotency_key,
    )

    existing = await session.get(JobDescription, job_id)
    if existing is not None:
        if existing.create_request_fingerprint != fingerprint:
            raise _idempotency_conflict()
        await _dispatch_if_pending(dispatcher, existing)
        return existing

    job = JobDescription(
        id=job_id,
        recruiter_id=current_user.id,
        title=payload.title,
        job_level=payload.job_level,
        location=payload.location,
        raw_content=payload.raw_content,
        create_request_fingerprint=fingerprint,
        revision=1,
        min_experience_years=Decimal("0.0"),
        education_requirement=None,
        job_embedding=None,
        embedding_model=None,
        embedding_preprocessing_version=None,
        parsing_status=ParsingStatus.PENDING.value,
        parsing_error_message=None,
        is_criteria_verified=False,
        w_skill=payload.w_skill,
        w_semantic=payload.w_semantic,
        w_experience=payload.w_experience,
        status=JobStatus.DRAFT.value,
        is_deleted=False,
        deleted_at=None,
        parsed_at=None,
    )
    session.add(job)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await session.get(JobDescription, job_id, populate_existing=True)
        if winner is None:
            raise
        if winner.create_request_fingerprint != fingerprint:
            raise _idempotency_conflict() from None
        await _dispatch_if_pending(dispatcher, winner)
        return winner
    except SQLAlchemyError:
        await session.rollback()
        raise

    await _dispatch_if_pending(dispatcher, job)
    return job


async def update_job(
    session: AsyncSession,
    *,
    current_user: User,
    job_id: uuid.UUID,
    payload: JobUpdateRequest,
    dispatcher: JobParseDispatcher,
) -> JobDescription:
    actor_id = current_user.id
    actor_role = current_user.role
    if actor_role not in {UserRole.HR.value, UserRole.ADMIN.value}:
        raise APIError(403, "INSUFFICIENT_PERMISSIONS", "Insufficient permissions")

    filters: list[ColumnElement[bool]] = [
        JobDescription.id == job_id,
        JobDescription.is_deleted.is_(False),
    ]
    if actor_role == UserRole.HR.value:
        filters.append(JobDescription.recruiter_id == actor_id)

    publication: tuple[uuid.UUID, int] | None = None
    try:
        with session.no_autoflush:
            job = await session.scalar(
                select(JobDescription)
                .where(*filters)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        if job is None:
            raise APIError(404, "JOB_NOT_FOUND", "Job not found")

        supplied = payload.model_fields_set
        raw_changed = "raw_content" in supplied and payload.raw_content != job.raw_content
        metadata_changes: dict[str, str | None] = {}
        for field in ("title", "job_level", "location"):
            if field in supplied:
                value = getattr(payload, field)
                if value != getattr(job, field):
                    metadata_changes[field] = value

        if not raw_changed and not metadata_changes:
            session.expunge(job)
            await session.rollback()
            return job

        if raw_changed:
            with session.no_autoflush:
                resume_ids = tuple(
                    (
                        await session.scalars(
                            select(MatchResult.resume_id)
                            .where(MatchResult.job_id == job.id)
                            .order_by(MatchResult.resume_id.asc())
                        )
                    ).all()
                )
                locked_resumes = (
                    tuple(
                        (
                            await session.scalars(
                                select(Resume)
                                .where(Resume.id.in_(resume_ids))
                                .order_by(Resume.id.asc())
                                .execution_options(populate_existing=True)
                                .with_for_update()
                            )
                        ).all()
                    )
                    if resume_ids
                    else ()
                )
                matches = tuple(
                    (
                        await session.scalars(
                            select(MatchResult)
                            .where(MatchResult.job_id == job.id)
                            .order_by(MatchResult.id.asc())
                            .execution_options(populate_existing=True)
                            .with_for_update()
                        )
                    ).all()
                )

            resume_revisions = {resume.id: resume.revision for resume in locked_resumes}
            now = datetime.now(UTC)
            for field, value in metadata_changes.items():
                setattr(job, field, value)
            job.raw_content = payload.raw_content
            job.revision += 1
            job.status = JobStatus.DRAFT.value
            job.parsing_status = ParsingStatus.PENDING.value
            job.is_criteria_verified = False
            job.job_embedding = None
            job.embedding_model = None
            job.embedding_preprocessing_version = None
            job.parsed_at = None
            job.parsing_error_message = None
            job.updated_at = now

            for match in matches:
                match.generation += 1
                match.resume_revision = resume_revisions[match.resume_id]
                match.job_revision = job.revision
                match.status = MatchStatus.PENDING.value
                match.overall_score = None
                match.skill_score = None
                match.semantic_score = None
                match.experience_score = None
                match.matched_skills = []
                match.missing_skills = []
                match.gap_analysis_summary = None
                match.error_message = None
                match.embedding_model = None
                match.embedding_preprocessing_version = None
                match.calculated_at = None
                match.updated_at = now
            publication = (job.id, job.revision)
        elif metadata_changes:
            for field, value in metadata_changes.items():
                setattr(job, field, value)
            job.updated_at = datetime.now(UTC)

        await session.flush()
        await session.commit()
    except APIError:
        await session.rollback()
        raise
    except Exception:  # noqa: BLE001 - atomic Job/Match mutation must fully rollback
        await session.rollback()
        raise

    if publication is not None:
        published_job_id, published_revision = publication
        try:
            await dispatcher.dispatch(published_job_id, published_revision)
        except Exception as error:  # noqa: BLE001 - committed PENDING revision is recoverable
            logger.error(
                "job_parse_dispatch_failed job_id=%s revision=%s error_type=%s",
                published_job_id,
                published_revision,
                type(error).__name__,
            )
    return job


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


def _managed_job_filters(current_user: User, job_id: uuid.UUID) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [
        JobDescription.id == job_id,
        JobDescription.is_deleted.is_(False),
    ]
    if current_user.role == UserRole.HR.value:
        filters.append(JobDescription.recruiter_id == current_user.id)
    return filters


async def get_job_criteria(
    session: AsyncSession,
    *,
    current_user: User,
    job_id: uuid.UUID,
) -> JobCriteriaRecord:
    job = await session.scalar(
        select(JobDescription).where(*_managed_job_filters(current_user, job_id))
    )
    if job is None:
        raise APIError(404, "JOB_NOT_FOUND", "Job not found")
    skills = tuple(
        (
            await session.scalars(
                select(JobSkill)
                .where(JobSkill.job_id == job.id)
                .order_by(JobSkill.skill_id.asc(), JobSkill.id.asc())
            )
        ).all()
    )
    return JobCriteriaRecord(job=job, skills=skills)


async def update_job_criteria(
    session: AsyncSession,
    *,
    current_user: User,
    job_id: uuid.UUID,
    payload: JobCriteriaRequest,
) -> JobCriteriaRecord:
    job = await session.scalar(
        select(JobDescription)
        .where(*_managed_job_filters(current_user, job_id))
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if job is None:
        raise APIError(404, "JOB_NOT_FOUND", "Job not found")
    if job.parsing_status != ParsingStatus.PARSED.value:
        raise APIError(
            422,
            "JOB_NOT_READY",
            "Job must be PARSED before criteria can be reviewed",
        )

    requested_skill_ids = {criterion.skill_id for criterion in payload.skills}
    existing_skill_ids = set(
        (await session.scalars(select(Skill.id).where(Skill.id.in_(requested_skill_ids)))).all()
    )
    if existing_skill_ids != requested_skill_ids:
        raise APIError(
            422,
            "INVALID_JOB_CRITERIA",
            "One or more criteria skills are not in the canonical taxonomy",
        )

    now = datetime.now(UTC)
    try:
        resume_ids = tuple(
            (
                await session.scalars(
                    select(MatchResult.resume_id)
                    .where(MatchResult.job_id == job.id)
                    .order_by(MatchResult.resume_id.asc())
                )
            ).all()
        )
        locked_resumes = (
            tuple(
                (
                    await session.scalars(
                        select(Resume)
                        .where(Resume.id.in_(resume_ids))
                        .order_by(Resume.id.asc())
                        .execution_options(populate_existing=True)
                        .with_for_update()
                    )
                ).all()
            )
            if resume_ids
            else ()
        )
        resume_revisions = {resume.id: resume.revision for resume in locked_resumes}
        matches = tuple(
            (
                await session.scalars(
                    select(MatchResult)
                    .where(MatchResult.job_id == job.id)
                    .order_by(MatchResult.id.asc())
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
            ).all()
        )

        await session.execute(delete(JobSkill).where(JobSkill.job_id == job.id))
        replacement_skills = tuple(
            JobSkill(
                job_id=job.id,
                skill_id=criterion.skill_id,
                importance=criterion.importance.value,
                min_years_required=criterion.min_years_required,
            )
            for criterion in sorted(payload.skills, key=lambda item: item.skill_id)
        )
        session.add_all(replacement_skills)

        if "min_experience_years" in payload.model_fields_set:
            job.min_experience_years = payload.min_experience_years
        if "education_requirement" in payload.model_fields_set:
            job.education_requirement = payload.education_requirement
        job.is_criteria_verified = True
        job.revision += 1
        job.updated_at = now

        for match in matches:
            match.generation += 1
            match.resume_revision = resume_revisions[match.resume_id]
            match.job_revision = job.revision
            match.status = MatchStatus.PENDING.value
            match.overall_score = None
            match.skill_score = None
            match.semantic_score = None
            match.experience_score = None
            match.matched_skills = []
            match.missing_skills = []
            match.gap_analysis_summary = None
            match.error_message = None
            match.embedding_model = None
            match.embedding_preprocessing_version = None
            match.calculated_at = None
            match.updated_at = now

        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        raise
    return JobCriteriaRecord(job=job, skills=replacement_skills)


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
