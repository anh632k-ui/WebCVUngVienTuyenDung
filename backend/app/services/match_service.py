from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.ai.matching_engine import ALGORITHM_VERSION
from app.core.exceptions import APIError
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume
from app.models.skill import JobSkill
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.match_schema import MatchCalculateRequest, MatchStatus
from app.services.match_dispatcher import MatchDispatcher

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchTaskPayload:
    match_id: uuid.UUID
    expected_generation: int
    expected_resume_revision: int
    expected_job_revision: int
    algorithm_version: str


def _validate_job_for_matching(job: JobDescription, current_user: User, *, has_skill: bool) -> None:
    if current_user.role == UserRole.CANDIDATE.value and job.status != "ACTIVE":
        raise APIError(404, "JOB_NOT_FOUND", "Job not found")
    if current_user.role == UserRole.HR.value and job.recruiter_id != current_user.id:
        raise APIError(404, "JOB_NOT_FOUND", "Job not found")
    if job.parsing_status != "PARSED" or not job.is_criteria_verified:
        raise APIError(422, "JOB_NOT_READY", "Job is not ready for matching")
    if (
        job.job_embedding is None
        or not job.embedding_model
        or not job.embedding_preprocessing_version
        or not has_skill
    ):
        raise APIError(422, "JOB_NOT_READY", "Job is not ready for matching")


def _validate_resumes_for_matching(
    resumes: list[Resume],
    current_user: User,
    job: JobDescription,
    *,
    requested_count: int,
) -> None:
    if len(resumes) != requested_count:
        raise APIError(404, "RESUME_NOT_FOUND", "One or more resumes were not found")
    for resume in resumes:
        if current_user.role != UserRole.ADMIN.value and resume.owner_user_id != current_user.id:
            raise APIError(404, "RESUME_NOT_FOUND", "One or more resumes were not found")
        if (
            resume.parsing_status != "PARSED"
            or resume.resume_embedding is None
            or not resume.embedding_model
            or not resume.embedding_preprocessing_version
        ):
            raise APIError(
                422,
                "RESUME_NOT_READY",
                "One or more resumes are not ready for matching",
            )
        if (
            resume.embedding_model != job.embedding_model
            or resume.embedding_preprocessing_version != job.embedding_preprocessing_version
        ):
            raise APIError(
                422,
                "EMBEDDING_VERSION_MISMATCH",
                "Resume and job embeddings are not compatible",
            )


def _reset_match(
    match: MatchResult,
    *,
    resume_revision: int,
    job_revision: int,
    now: datetime,
) -> None:
    match.generation += 1
    match.resume_revision = resume_revision
    match.job_revision = job_revision
    match.algorithm_version = ALGORITHM_VERSION
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


async def _prepare_match_batch(
    session: AsyncSession,
    *,
    current_user: User,
    payload: MatchCalculateRequest,
) -> tuple[MatchTaskPayload, ...]:
    """Validate and prepare the full batch in the session's current transaction."""

    job = await session.scalar(
        select(JobDescription)
        .where(JobDescription.id == payload.job_id, JobDescription.is_deleted.is_(False))
        .with_for_update()
    )
    if job is None:
        raise APIError(404, "JOB_NOT_FOUND", "Job not found")

    ordered_resume_ids = sorted(payload.resume_ids, key=lambda item: item.int)
    resumes = list(
        (
            await session.scalars(
                select(Resume)
                .where(Resume.id.in_(ordered_resume_ids), Resume.is_deleted.is_(False))
                .order_by(Resume.id.asc())
                .with_for_update()
            )
        ).all()
    )
    has_skill = bool(
        await session.scalar(select(select(JobSkill.id).where(JobSkill.job_id == job.id).exists()))
    )

    # No Match row is selected or changed until every resource in the batch passes.
    _validate_job_for_matching(job, current_user, has_skill=has_skill)
    _validate_resumes_for_matching(
        resumes,
        current_user,
        job,
        requested_count=len(payload.resume_ids),
    )

    existing_matches = list(
        (
            await session.scalars(
                select(MatchResult)
                .where(
                    MatchResult.job_id == job.id,
                    MatchResult.resume_id.in_(ordered_resume_ids),
                )
                .order_by(MatchResult.id.asc())
                .with_for_update()
            )
        ).all()
    )
    matches_by_resume = {match.resume_id: match for match in existing_matches}
    resumes_by_id = {resume.id: resume for resume in resumes}
    now = datetime.now(UTC)

    for resume_id in payload.resume_ids:
        resume = resumes_by_id[resume_id]
        match = matches_by_resume.get(resume_id)
        if match is None:
            match = MatchResult(
                id=uuid.uuid4(),
                job_id=job.id,
                resume_id=resume.id,
                generation=1,
                resume_revision=resume.revision,
                job_revision=job.revision,
                overall_score=None,
                skill_score=None,
                semantic_score=None,
                experience_score=None,
                matched_skills=[],
                missing_skills=[],
                gap_analysis_summary=None,
                algorithm_version=ALGORITHM_VERSION,
                embedding_model=None,
                embedding_preprocessing_version=None,
                status=MatchStatus.PENDING.value,
                error_message=None,
                created_at=now,
                updated_at=now,
                calculated_at=None,
            )
            session.add(match)
            matches_by_resume[resume_id] = match
        else:
            _reset_match(
                match,
                resume_revision=resume.revision,
                job_revision=job.revision,
                now=now,
            )

    await session.flush()
    return tuple(
        MatchTaskPayload(
            match_id=matches_by_resume[resume_id].id,
            expected_generation=matches_by_resume[resume_id].generation,
            expected_resume_revision=matches_by_resume[resume_id].resume_revision,
            expected_job_revision=matches_by_resume[resume_id].job_revision,
            algorithm_version=matches_by_resume[resume_id].algorithm_version,
        )
        for resume_id in payload.resume_ids
    )


async def calculate_matches(
    session: AsyncSession,
    *,
    current_user: User,
    payload: MatchCalculateRequest,
    dispatcher: MatchDispatcher,
) -> list[uuid.UUID]:
    try:
        task_payloads = await _prepare_match_batch(
            session,
            current_user=current_user,
            payload=payload,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    for task_payload in task_payloads:
        try:
            await dispatcher.dispatch(
                task_payload.match_id,
                task_payload.expected_generation,
                task_payload.expected_resume_revision,
                task_payload.expected_job_revision,
                task_payload.algorithm_version,
            )
        except Exception as error:
            logger.error(
                "match_trigger_dispatch_failed match_id=%s generation=%s",
                task_payload.match_id,
                task_payload.expected_generation,
            )
            raise APIError(
                503,
                "TASK_DISPATCH_FAILED",
                "Match task dispatch failed",
            ) from error
    return [task_payload.match_id for task_payload in task_payloads]


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
