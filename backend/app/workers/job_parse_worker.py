from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.job_parser import JobParseError
from app.ai.job_schemas import ParsedJobResult
from app.ai.parser_isolation import ParserIsolationTimeout, run_job_parser_isolated
from app.ai.resume_parser import RESUME_TEXT_PREPROCESSING_VERSION
from app.ai.schemas import TaxonomySkill
from app.ai.vector_embedding import (
    BGE_M3_EMBEDDING_DIMENSION,
    BGE_M3_MODEL_NAME,
    EmbeddingProvider,
    EmbeddingProviderError,
    validate_dense_embedding,
)
from app.core.processing_fencing import (
    ProcessingAttemptGuard,
    claim_job_attempt,
    job_fence,
    terminal_ownership_values,
    unowned_processing,
)
from app.core.version_guard import claim_job_revision, fail_job_revision
from app.models.job import JobDescription
from app.models.skill import JobSkill, Skill

logger = logging.getLogger(__name__)

MAX_WORKER_ERROR_MESSAGE_LENGTH = 500
MAX_EDUCATION_REQUIREMENT_LENGTH = 255

SessionFactory = Callable[[], AsyncSession]


class JobParseTaskOutcome(StrEnum):
    PARSED = "PARSED"
    FAILED = "FAILED"
    DISCARDED = "DISCARDED"


@dataclass(frozen=True)
class JobParseContext:
    raw_content: str
    taxonomy: tuple[TaxonomySkill, ...]


def _bounded_error_message(message: str) -> str:
    return message[:MAX_WORKER_ERROR_MESSAGE_LENGTH]


async def _load_parse_context(
    session_factory: SessionFactory,
    *,
    job_id: uuid.UUID,
    expected_revision: int,
    attempt_guard: ProcessingAttemptGuard | None = None,
) -> JobParseContext | None:
    async with session_factory() as session:
        ownership_guard = (
            job_fence(attempt_guard)
            if attempt_guard is not None
            else unowned_processing(JobDescription)
        )
        raw_content = await session.scalar(
            select(JobDescription.raw_content).where(
                JobDescription.id == job_id,
                JobDescription.revision == expected_revision,
                JobDescription.parsing_status == "PROCESSING",
                JobDescription.is_deleted.is_(False),
                *ownership_guard,
            )
        )
        if raw_content is None:
            return None
        skills = (await session.scalars(select(Skill).order_by(Skill.id.asc()))).all()
        if not skills:
            raise RuntimeError("Canonical skill taxonomy is empty")
        taxonomy = tuple(
            TaxonomySkill(
                id=skill.id,
                name=skill.name,
                normalized_name=skill.normalized_name,
                skill_kind=cast(Literal["HARD", "SOFT"], skill.skill_kind),
                category=skill.category,
            )
            for skill in skills
        )
        return JobParseContext(raw_content=raw_content, taxonomy=taxonomy)


def _valid_nonnegative_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _validate_parsed_result(
    parsed: ParsedJobResult,
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    if not isinstance(parsed.normalized_text, str) or not any(
        character.isalnum() for character in parsed.normalized_text
    ):
        raise RuntimeError("Parsed Job normalized text is invalid")
    if not _valid_nonnegative_decimal(parsed.min_experience_years):
        raise RuntimeError("Parsed Job experience requirement is invalid")
    if parsed.education_requirement is not None and (
        not isinstance(parsed.education_requirement, str)
        or len(parsed.education_requirement) > MAX_EDUCATION_REQUIREMENT_LENGTH
    ):
        raise RuntimeError("Parsed Job education requirement is invalid")

    taxonomy_ids = {skill.id for skill in taxonomy}
    parsed_ids = [skill.skill_id for skill in parsed.skills]
    if len(parsed_ids) != len(set(parsed_ids)) or not set(parsed_ids).issubset(taxonomy_ids):
        raise RuntimeError("Parsed Job skills do not map uniquely to the canonical taxonomy")
    for skill in parsed.skills:
        if skill.importance not in {"MANDATORY", "OPTIONAL"}:
            raise RuntimeError("Parsed Job skill importance is invalid")
        if not _valid_nonnegative_decimal(skill.min_years_required):
            raise RuntimeError("Parsed Job skill experience requirement is invalid")


async def _persist_success(
    session_factory: SessionFactory,
    *,
    job_id: uuid.UUID,
    expected_revision: int,
    parsed: ParsedJobResult,
    embedding: list[float],
    attempt_guard: ProcessingAttemptGuard | None = None,
) -> bool:
    now = datetime.now(UTC)
    async with session_factory() as session:
        try:
            if attempt_guard is not None:
                owns_attempt = await session.scalar(
                    select(JobDescription.id).where(*job_fence(attempt_guard)).with_for_update()
                )
                if owns_attempt is None:
                    await session.rollback()
                    return False
                await session.execute(delete(JobSkill).where(JobSkill.job_id == job_id))
                session.add_all(
                    JobSkill(
                        job_id=job_id,
                        skill_id=skill.skill_id,
                        importance=skill.importance,
                        min_years_required=skill.min_years_required,
                    )
                    for skill in parsed.skills
                )
                await session.flush()
                result = await session.execute(
                    update(JobDescription)
                    .where(*job_fence(attempt_guard))
                    .values(
                        min_experience_years=parsed.min_experience_years,
                        education_requirement=parsed.education_requirement,
                        job_embedding=embedding,
                        embedding_model=BGE_M3_MODEL_NAME,
                        embedding_preprocessing_version=RESUME_TEXT_PREPROCESSING_VERSION,
                        parsed_at=func.clock_timestamp(),
                        parsing_error_message=None,
                        updated_at=func.clock_timestamp(),
                        parsing_status="PARSED",
                        **terminal_ownership_values(),
                    )
                    .returning(JobDescription.id)
                )
                if result.scalar_one_or_none() is None:
                    await session.rollback()
                    return False
                await session.commit()
                return True

            result = await session.execute(
                update(JobDescription)
                .where(
                    JobDescription.id == job_id,
                    JobDescription.revision == expected_revision,
                    JobDescription.parsing_status == "PROCESSING",
                    JobDescription.is_deleted.is_(False),
                    *unowned_processing(JobDescription),
                )
                .values(
                    min_experience_years=parsed.min_experience_years,
                    education_requirement=parsed.education_requirement,
                    job_embedding=embedding,
                    embedding_model=BGE_M3_MODEL_NAME,
                    embedding_preprocessing_version=RESUME_TEXT_PREPROCESSING_VERSION,
                    parsed_at=now,
                    parsing_error_message=None,
                    updated_at=now,
                    parsing_status="PARSED",
                )
                .returning(JobDescription.id)
            )
            if result.scalar_one_or_none() is None:
                await session.rollback()
                return False

            await session.execute(delete(JobSkill).where(JobSkill.job_id == job_id))
            session.add_all(
                JobSkill(
                    job_id=job_id,
                    skill_id=skill.skill_id,
                    importance=skill.importance,
                    min_years_required=skill.min_years_required,
                )
                for skill in parsed.skills
            )
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise RuntimeError("Job terminal persistence failed") from None


async def _mark_failed(
    session_factory: SessionFactory,
    *,
    job_id: uuid.UUID,
    expected_revision: int,
    message: str,
    attempt_guard: ProcessingAttemptGuard | None = None,
) -> JobParseTaskOutcome:
    try:
        async with session_factory() as session:
            if attempt_guard is None:
                failed = await fail_job_revision(
                    session,
                    job_id=job_id,
                    expected_revision=expected_revision,
                    error_message=_bounded_error_message(message),
                )
            else:
                result = await session.execute(
                    update(JobDescription)
                    .where(*job_fence(attempt_guard))
                    .values(
                        parsing_status="FAILED",
                        parsing_error_message=_bounded_error_message(message),
                        updated_at=func.clock_timestamp(),
                        **terminal_ownership_values(),
                    )
                    .returning(JobDescription.id)
                )
                failed = result.scalar_one_or_none() is not None
                if failed:
                    await session.commit()
                else:
                    await session.rollback()
    except Exception:  # noqa: BLE001 - failure CAS is best-effort after a claimed task failure
        logger.error(
            "job_parse_failure_cas_error job_id=%s expected_revision=%s",
            job_id,
            expected_revision,
        )
        return JobParseTaskOutcome.DISCARDED
    return JobParseTaskOutcome.FAILED if failed else JobParseTaskOutcome.DISCARDED


def _log_outcome(
    job_id: uuid.UUID,
    expected_revision: int,
    outcome: JobParseTaskOutcome,
) -> JobParseTaskOutcome:
    logger.info(
        "job_parse_task job_id=%s expected_revision=%s outcome=%s",
        job_id,
        expected_revision,
        outcome.value,
    )
    return outcome


async def process_job_parse_task(
    job_id: uuid.UUID,
    expected_revision: int,
    *,
    session_factory: SessionFactory,
    embedding_provider: EmbeddingProvider,
    processing_fencing_enabled: bool = False,
    processing_fencing_lease_seconds: int = 1_800,
) -> JobParseTaskOutcome:
    async with session_factory() as claim_session:
        attempt_guard = None
        if processing_fencing_enabled:
            attempt_guard = await claim_job_attempt(
                claim_session,
                job_id=job_id,
                expected_revision=expected_revision,
                lease_seconds=processing_fencing_lease_seconds,
            )
            claimed = attempt_guard is not None
        else:
            claimed = await claim_job_revision(
                claim_session,
                job_id=job_id,
                expected_revision=expected_revision,
            )
    if not claimed:
        return _log_outcome(job_id, expected_revision, JobParseTaskOutcome.DISCARDED)

    try:
        context = await _load_parse_context(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            attempt_guard=attempt_guard,
        )
    except Exception:  # noqa: BLE001 - taxonomy/context failures become controlled FAILED
        outcome = await _mark_failed(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            message="Job taxonomy or content could not be loaded",
            attempt_guard=attempt_guard,
        )
        return _log_outcome(job_id, expected_revision, outcome)
    if context is None:
        return _log_outcome(job_id, expected_revision, JobParseTaskOutcome.DISCARDED)

    try:
        parsed = await run_job_parser_isolated(context.raw_content, context.taxonomy)
        _validate_parsed_result(parsed, context.taxonomy)
    except ParserIsolationTimeout:
        outcome = await _mark_failed(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            message="Job parsing timed out",
            attempt_guard=attempt_guard,
        )
        return _log_outcome(job_id, expected_revision, outcome)
    except JobParseError as error:
        outcome = await _mark_failed(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            message=f"Job parsing failed: {error.kind.value}",
            attempt_guard=attempt_guard,
        )
        return _log_outcome(job_id, expected_revision, outcome)
    except Exception:  # noqa: BLE001 - parser details must not enter the persisted message
        outcome = await _mark_failed(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            message="Job parsing failed",
            attempt_guard=attempt_guard,
        )
        return _log_outcome(job_id, expected_revision, outcome)

    try:
        if (
            embedding_provider.model_name != BGE_M3_MODEL_NAME
            or embedding_provider.dimension != BGE_M3_EMBEDDING_DIMENSION
        ):
            raise EmbeddingProviderError("Embedding provider metadata is incompatible")
        embedding = validate_dense_embedding(
            await embedding_provider.embed(parsed.normalized_text),
            expected_dimension=BGE_M3_EMBEDDING_DIMENSION,
        )
    except Exception:  # noqa: BLE001 - model/runtime details must not enter persisted messages
        outcome = await _mark_failed(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            message="Job embedding failed",
            attempt_guard=attempt_guard,
        )
        return _log_outcome(job_id, expected_revision, outcome)

    try:
        persisted = await _persist_success(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            parsed=parsed,
            embedding=embedding,
            attempt_guard=attempt_guard,
        )
    except Exception:  # noqa: BLE001 - persistence failures become controlled FAILED
        outcome = await _mark_failed(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            message="Job parsed aggregate could not be persisted",
            attempt_guard=attempt_guard,
        )
        return _log_outcome(job_id, expected_revision, outcome)
    outcome = JobParseTaskOutcome.PARSED if persisted else JobParseTaskOutcome.DISCARDED
    return _log_outcome(job_id, expected_revision, outcome)
