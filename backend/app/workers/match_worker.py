from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypedDict, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.matching_engine import ALGORITHM_VERSION, MatchComputationError, compute_match
from app.ai.matching_schemas import (
    CandidateExperience,
    CandidateSkill,
    EmbeddingInput,
    JobSkillRequirement,
    MatchComputationResult,
    MatchWeights,
    SkillEvidence,
    SkillImportance,
)
from app.core.processing_fencing import (
    ProcessingAttemptGuard,
    claim_match_attempt,
    match_fence,
    terminal_ownership_values,
)
from app.core.version_guard import claim_match_generation
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume, ResumeExperience
from app.models.skill import JobSkill, ResumeSkill, Skill

logger = logging.getLogger(__name__)

MAX_MATCH_WORKER_ERROR_MESSAGE_LENGTH = 500

SessionFactory = Callable[[], AsyncSession]
DateProvider = Callable[[], date]


class MatchTaskOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DISCARDED = "DISCARDED"


class MatchContextError(RuntimeError):
    """A controlled failure caused by invalid persisted scoring context."""


class MatchTaskGuard(TypedDict):
    match_id: uuid.UUID
    expected_generation: int
    expected_resume_revision: int
    expected_job_revision: int
    algorithm_version: str
    attempt_guard: ProcessingAttemptGuard | None


@dataclass(frozen=True)
class MatchComputationContext:
    resume_id: uuid.UUID
    job_id: uuid.UUID
    candidate_skills: tuple[CandidateSkill, ...]
    job_skills: tuple[JobSkillRequirement, ...]
    candidate_experiences: tuple[CandidateExperience, ...]
    min_experience_years: Decimal
    weights: MatchWeights
    embeddings: EmbeddingInput


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _bounded_error_message(message: str) -> str:
    return message[:MAX_MATCH_WORKER_ERROR_MESSAGE_LENGTH]


def _as_vector(value: Sequence[float] | None) -> Sequence[float] | None:
    return tuple(value) if value is not None else None


async def _load_computation_context(
    session_factory: SessionFactory,
    *,
    match_id: uuid.UUID,
    expected_generation: int,
    expected_resume_revision: int,
    expected_job_revision: int,
    algorithm_version: str,
    attempt_guard: ProcessingAttemptGuard | None = None,
) -> MatchComputationContext | None:
    async with session_factory() as session:
        ownership_guard = (
            match_fence(attempt_guard)
            if attempt_guard is not None
            else (
                MatchResult.processing_attempt_epoch == 0,
                MatchResult.processing_owner_token.is_(None),
                MatchResult.processing_activation_token.is_(None),
                MatchResult.processing_heartbeat_at.is_(None),
                MatchResult.processing_lease_expires_at.is_(None),
            )
        )
        row = (
            await session.execute(
                select(MatchResult, Resume, JobDescription)
                .join(Resume, Resume.id == MatchResult.resume_id)
                .join(JobDescription, JobDescription.id == MatchResult.job_id)
                .where(
                    MatchResult.id == match_id,
                    MatchResult.generation == expected_generation,
                    MatchResult.resume_revision == expected_resume_revision,
                    MatchResult.job_revision == expected_job_revision,
                    MatchResult.algorithm_version == algorithm_version,
                    MatchResult.status == "PROCESSING",
                    *ownership_guard,
                    Resume.revision == expected_resume_revision,
                    Resume.is_deleted.is_(False),
                    JobDescription.revision == expected_job_revision,
                    JobDescription.is_deleted.is_(False),
                )
            )
        ).one_or_none()
        if row is None:
            return None
        match, resume, job = row

        if resume.parsing_status != "PARSED":
            raise MatchContextError("Resume is not ready for matching")
        if job.parsing_status != "PARSED" or not job.is_criteria_verified:
            raise MatchContextError("Job criteria are not ready for matching")

        resume_skill_rows = (
            await session.execute(
                select(ResumeSkill, Skill.name)
                .join(Skill, Skill.id == ResumeSkill.skill_id)
                .where(ResumeSkill.resume_id == resume.id)
                .order_by(ResumeSkill.skill_id.asc())
            )
        ).all()
        job_skill_rows = (
            await session.execute(
                select(JobSkill, Skill.name)
                .join(Skill, Skill.id == JobSkill.skill_id)
                .where(JobSkill.job_id == job.id)
                .order_by(JobSkill.skill_id.asc())
            )
        ).all()
        if not job_skill_rows:
            raise MatchContextError("Job has no verified skill criteria")

        experience_rows = (
            await session.scalars(
                select(ResumeExperience)
                .where(ResumeExperience.resume_id == resume.id)
                .order_by(ResumeExperience.id.asc())
            )
        ).all()

        candidate_skills = tuple(
            CandidateSkill(
                skill_id=item.skill_id,
                name=name,
                years_of_experience=item.years_of_experience,
            )
            for item, name in resume_skill_rows
        )
        try:
            job_skills = tuple(
                JobSkillRequirement(
                    skill_id=item.skill_id,
                    name=name,
                    importance=SkillImportance(item.importance),
                    min_years_required=item.min_years_required,
                )
                for item, name in job_skill_rows
            )
        except ValueError as error:
            raise MatchContextError("Job skill criteria are invalid") from error

        return MatchComputationContext(
            resume_id=resume.id,
            job_id=job.id,
            candidate_skills=candidate_skills,
            job_skills=job_skills,
            candidate_experiences=tuple(
                CandidateExperience(
                    start_date=item.start_date,
                    end_date=item.end_date,
                    is_current=item.is_current,
                )
                for item in experience_rows
            ),
            min_experience_years=job.min_experience_years,
            weights=MatchWeights(
                skill=job.w_skill,
                semantic=job.w_semantic,
                experience=job.w_experience,
            ),
            embeddings=EmbeddingInput(
                resume_vector=_as_vector(resume.resume_embedding),
                job_vector=_as_vector(job.job_embedding),
                resume_model=resume.embedding_model or "",
                job_model=job.embedding_model or "",
                resume_preprocessing_version=resume.embedding_preprocessing_version or "",
                job_preprocessing_version=job.embedding_preprocessing_version or "",
            ),
        )


def _serialize_evidence(evidence: SkillEvidence) -> dict[str, Any]:
    return {
        "skill_id": evidence.skill_id,
        "name": evidence.name,
        "importance": evidence.importance.value,
        "status": evidence.status.value,
        "candidate_years": (
            float(evidence.candidate_years) if evidence.candidate_years is not None else None
        ),
        "required_years": float(evidence.required_years),
        "severity": evidence.severity.value if evidence.severity is not None else None,
    }


async def _terminal_resource_ids(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    expected_generation: int,
    expected_resume_revision: int,
    expected_job_revision: int,
    algorithm_version: str,
    attempt_guard: ProcessingAttemptGuard | None = None,
) -> tuple[uuid.UUID, uuid.UUID] | None:
    ownership_guard = (
        match_fence(attempt_guard)
        if attempt_guard is not None
        else (
            MatchResult.processing_attempt_epoch == 0,
            MatchResult.processing_owner_token.is_(None),
            MatchResult.processing_activation_token.is_(None),
            MatchResult.processing_heartbeat_at.is_(None),
            MatchResult.processing_lease_expires_at.is_(None),
        )
    )
    row = (
        await session.execute(
            select(MatchResult.resume_id, MatchResult.job_id).where(
                MatchResult.id == match_id,
                MatchResult.generation == expected_generation,
                MatchResult.resume_revision == expected_resume_revision,
                MatchResult.job_revision == expected_job_revision,
                MatchResult.algorithm_version == algorithm_version,
                MatchResult.status == "PROCESSING",
                *ownership_guard,
            )
        )
    ).one_or_none()
    return cast(tuple[uuid.UUID, uuid.UUID] | None, row)


async def _lock_current_resources(
    session: AsyncSession,
    *,
    resume_id: uuid.UUID,
    job_id: uuid.UUID,
    expected_resume_revision: int,
    expected_job_revision: int,
) -> bool:
    # Criteria updates also lock Job first. Keep this order for both terminal
    # outcomes so an invalidation cannot hold Job while waiting on our Resume.
    job = await session.scalar(
        select(JobDescription).where(JobDescription.id == job_id).with_for_update()
    )
    if (
        job is None
        or job.revision != expected_job_revision
        or job.is_deleted
        or job.parsing_status != "PARSED"
        or not job.is_criteria_verified
    ):
        return False
    resume = await session.scalar(select(Resume).where(Resume.id == resume_id).with_for_update())
    return (
        resume is not None
        and resume.revision == expected_resume_revision
        and not resume.is_deleted
        and resume.parsing_status == "PARSED"
    )


def _terminal_guard(
    *,
    match_id: uuid.UUID,
    resume_id: uuid.UUID,
    job_id: uuid.UUID,
    expected_generation: int,
    expected_resume_revision: int,
    expected_job_revision: int,
    algorithm_version: str,
    attempt_guard: ProcessingAttemptGuard | None = None,
) -> tuple[Any, ...]:
    base = (
        MatchResult.id == match_id,
        MatchResult.resume_id == resume_id,
        MatchResult.job_id == job_id,
        MatchResult.generation == expected_generation,
        MatchResult.resume_revision == expected_resume_revision,
        MatchResult.job_revision == expected_job_revision,
        MatchResult.algorithm_version == algorithm_version,
        MatchResult.status == "PROCESSING",
    )
    if attempt_guard is not None:
        return (*base, *match_fence(attempt_guard))
    return (
        *base,
        MatchResult.processing_attempt_epoch == 0,
        MatchResult.processing_owner_token.is_(None),
        MatchResult.processing_activation_token.is_(None),
        MatchResult.processing_heartbeat_at.is_(None),
        MatchResult.processing_lease_expires_at.is_(None),
    )


async def _terminal_update(
    session_factory: SessionFactory,
    *,
    match_id: uuid.UUID,
    expected_generation: int,
    expected_resume_revision: int,
    expected_job_revision: int,
    algorithm_version: str,
    values: dict[str, Any],
    attempt_guard: ProcessingAttemptGuard | None = None,
) -> bool:
    async with session_factory() as session:
        try:
            resource_ids = await _terminal_resource_ids(
                session,
                match_id=match_id,
                expected_generation=expected_generation,
                expected_resume_revision=expected_resume_revision,
                expected_job_revision=expected_job_revision,
                algorithm_version=algorithm_version,
                attempt_guard=attempt_guard,
            )
            if resource_ids is None:
                await session.rollback()
                return False
            resume_id, job_id = resource_ids
            if not await _lock_current_resources(
                session,
                resume_id=resume_id,
                job_id=job_id,
                expected_resume_revision=expected_resume_revision,
                expected_job_revision=expected_job_revision,
            ):
                await session.rollback()
                return False
            result = await session.execute(
                update(MatchResult)
                .where(
                    *_terminal_guard(
                        match_id=match_id,
                        resume_id=resume_id,
                        job_id=job_id,
                        expected_generation=expected_generation,
                        expected_resume_revision=expected_resume_revision,
                        expected_job_revision=expected_job_revision,
                        algorithm_version=algorithm_version,
                        attempt_guard=attempt_guard,
                    )
                )
                .values(
                    **values,
                    **(terminal_ownership_values() if attempt_guard is not None else {}),
                )
                .returning(MatchResult.id)
            )
            if result.scalar_one_or_none() is None:
                await session.rollback()
                return False
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise RuntimeError("Match terminal persistence failed") from None


async def _persist_success(
    session_factory: SessionFactory,
    *,
    match_id: uuid.UUID,
    expected_generation: int,
    expected_resume_revision: int,
    expected_job_revision: int,
    algorithm_version: str,
    context: MatchComputationContext,
    computed: MatchComputationResult,
    attempt_guard: ProcessingAttemptGuard | None = None,
) -> bool:
    now = datetime.now(UTC)
    return await _terminal_update(
        session_factory,
        match_id=match_id,
        expected_generation=expected_generation,
        expected_resume_revision=expected_resume_revision,
        expected_job_revision=expected_job_revision,
        algorithm_version=algorithm_version,
        attempt_guard=attempt_guard,
        values={
            "status": "COMPLETED",
            "overall_score": computed.overall_score,
            "skill_score": computed.skill_score,
            "semantic_score": computed.semantic_score,
            "experience_score": computed.experience_score,
            "matched_skills": [_serialize_evidence(item) for item in computed.matched_skills],
            "missing_skills": [_serialize_evidence(item) for item in computed.missing_skills],
            "gap_analysis_summary": None,
            "algorithm_version": computed.algorithm_version,
            "embedding_model": context.embeddings.resume_model,
            "embedding_preprocessing_version": (context.embeddings.resume_preprocessing_version),
            "error_message": None,
            "calculated_at": now,
            "updated_at": now,
        },
    )


async def _mark_failed(
    session_factory: SessionFactory,
    *,
    match_id: uuid.UUID,
    expected_generation: int,
    expected_resume_revision: int,
    expected_job_revision: int,
    algorithm_version: str,
    message: str,
    attempt_guard: ProcessingAttemptGuard | None = None,
) -> MatchTaskOutcome:
    now = datetime.now(UTC)
    try:
        failed = await _terminal_update(
            session_factory,
            match_id=match_id,
            expected_generation=expected_generation,
            expected_resume_revision=expected_resume_revision,
            expected_job_revision=expected_job_revision,
            algorithm_version=algorithm_version,
            attempt_guard=attempt_guard,
            values={
                "status": "FAILED",
                "overall_score": None,
                "skill_score": None,
                "semantic_score": None,
                "experience_score": None,
                "matched_skills": [],
                "missing_skills": [],
                "gap_analysis_summary": None,
                "embedding_model": None,
                "embedding_preprocessing_version": None,
                "error_message": _bounded_error_message(message),
                "calculated_at": None,
                "updated_at": now,
            },
        )
    except Exception:  # noqa: BLE001 - terminal failure reporting is best-effort
        logger.error(
            "match_failure_cas_error match_id=%s expected_generation=%s",
            match_id,
            expected_generation,
        )
        return MatchTaskOutcome.DISCARDED
    return MatchTaskOutcome.FAILED if failed else MatchTaskOutcome.DISCARDED


def _log_outcome(
    match_id: uuid.UUID,
    expected_generation: int,
    outcome: MatchTaskOutcome,
) -> MatchTaskOutcome:
    logger.info(
        "match_task match_id=%s expected_generation=%s outcome=%s",
        match_id,
        expected_generation,
        outcome.value,
    )
    return outcome


async def process_match_task(
    match_id: uuid.UUID,
    expected_generation: int,
    expected_resume_revision: int,
    expected_job_revision: int,
    algorithm_version: str,
    *,
    session_factory: SessionFactory,
    date_provider: DateProvider = _utc_today,
    processing_fencing_enabled: bool = False,
    processing_fencing_lease_seconds: int = 1_800,
) -> MatchTaskOutcome:
    if algorithm_version != ALGORITHM_VERSION:
        return _log_outcome(match_id, expected_generation, MatchTaskOutcome.DISCARDED)

    async with session_factory() as claim_session:
        attempt_guard = None
        if processing_fencing_enabled:
            attempt_guard = await claim_match_attempt(
                claim_session,
                match_id=match_id,
                expected_generation=expected_generation,
                expected_resume_revision=expected_resume_revision,
                expected_job_revision=expected_job_revision,
                algorithm_version=algorithm_version,
                lease_seconds=processing_fencing_lease_seconds,
            )
            claimed = attempt_guard is not None
        else:
            claimed = await claim_match_generation(
                claim_session,
                match_id=match_id,
                expected_generation=expected_generation,
                expected_resume_revision=expected_resume_revision,
                expected_job_revision=expected_job_revision,
                algorithm_version=algorithm_version,
            )
    if not claimed:
        return _log_outcome(match_id, expected_generation, MatchTaskOutcome.DISCARDED)

    task: MatchTaskGuard = {
        "match_id": match_id,
        "expected_generation": expected_generation,
        "expected_resume_revision": expected_resume_revision,
        "expected_job_revision": expected_job_revision,
        "algorithm_version": algorithm_version,
        "attempt_guard": attempt_guard,
    }
    try:
        context = await _load_computation_context(session_factory, **task)
    except MatchContextError:
        outcome = await _mark_failed(
            session_factory, **task, message="Match scoring context is not ready"
        )
        return _log_outcome(match_id, expected_generation, outcome)
    except Exception:  # noqa: BLE001 - persistence details must not leak to Match errors
        outcome = await _mark_failed(
            session_factory, **task, message="Match scoring context could not be loaded"
        )
        return _log_outcome(match_id, expected_generation, outcome)
    if context is None:
        return _log_outcome(match_id, expected_generation, MatchTaskOutcome.DISCARDED)

    try:
        as_of_date = date_provider()
        computed = compute_match(
            candidate_skills=context.candidate_skills,
            job_skills=context.job_skills,
            candidate_experiences=context.candidate_experiences,
            min_experience_years=context.min_experience_years,
            weights=context.weights,
            embeddings=context.embeddings,
            as_of_date=as_of_date,
        )
    except MatchComputationError as error:
        outcome = await _mark_failed(
            session_factory,
            **task,
            message=f"Match computation failed: {error.kind.value}",
        )
        return _log_outcome(match_id, expected_generation, outcome)
    except Exception:  # noqa: BLE001 - runtime details must not leak to Match errors
        outcome = await _mark_failed(session_factory, **task, message="Match computation failed")
        return _log_outcome(match_id, expected_generation, outcome)

    try:
        persisted = await _persist_success(
            session_factory,
            **task,
            context=context,
            computed=computed,
        )
    except Exception:  # noqa: BLE001 - terminal persistence errors use guarded FAILED CAS
        outcome = await _mark_failed(
            session_factory, **task, message="Match result could not be persisted"
        )
        return _log_outcome(match_id, expected_generation, outcome)
    outcome = MatchTaskOutcome.COMPLETED if persisted else MatchTaskOutcome.DISCARDED
    return _log_outcome(match_id, expected_generation, outcome)
