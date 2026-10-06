from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, cast

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.job_parser import JobParseError, parse_job_description
from app.ai.job_schemas import ParsedJobResult
from app.ai.resume_parser import RESUME_TEXT_PREPROCESSING_VERSION
from app.ai.schemas import TaxonomySkill
from app.ai.vector_embedding import (
    BGE_M3_EMBEDDING_DIMENSION,
    BGE_M3_MODEL_NAME,
    EmbeddingProvider,
    EmbeddingProviderError,
    validate_dense_embedding,
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
) -> JobParseContext | None:
    async with session_factory() as session:
        raw_content = await session.scalar(
            select(JobDescription.raw_content).where(
                JobDescription.id == job_id,
                JobDescription.revision == expected_revision,
                JobDescription.parsing_status == "PROCESSING",
                JobDescription.is_deleted.is_(False),
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
) -> bool:
    now = datetime.now(UTC)
    async with session_factory() as session:
        try:
            result = await session.execute(
                update(JobDescription)
                .where(
                    JobDescription.id == job_id,
                    JobDescription.revision == expected_revision,
                    JobDescription.parsing_status == "PROCESSING",
                    JobDescription.is_deleted.is_(False),
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
            raise


async def _mark_failed(
    session_factory: SessionFactory,
    *,
    job_id: uuid.UUID,
    expected_revision: int,
    message: str,
) -> JobParseTaskOutcome:
    try:
        async with session_factory() as session:
            failed = await fail_job_revision(
                session,
                job_id=job_id,
                expected_revision=expected_revision,
                error_message=_bounded_error_message(message),
            )
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
) -> JobParseTaskOutcome:
    async with session_factory() as claim_session:
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
        )
    except Exception:  # noqa: BLE001 - taxonomy/context failures become controlled FAILED
        outcome = await _mark_failed(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            message="Job taxonomy or content could not be loaded",
        )
        return _log_outcome(job_id, expected_revision, outcome)
    if context is None:
        return _log_outcome(job_id, expected_revision, JobParseTaskOutcome.DISCARDED)

    try:
        parsed = await asyncio.to_thread(
            parse_job_description,
            context.raw_content,
            context.taxonomy,
        )
        _validate_parsed_result(parsed, context.taxonomy)
    except JobParseError as error:
        outcome = await _mark_failed(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            message=f"Job parsing failed: {error.kind.value}",
        )
        return _log_outcome(job_id, expected_revision, outcome)
    except Exception:  # noqa: BLE001 - parser details must not enter the persisted message
        outcome = await _mark_failed(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            message="Job parsing failed",
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
        )
        return _log_outcome(job_id, expected_revision, outcome)

    try:
        persisted = await _persist_success(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            parsed=parsed,
            embedding=embedding,
        )
    except Exception:  # noqa: BLE001 - persistence failures become controlled FAILED
        outcome = await _mark_failed(
            session_factory,
            job_id=job_id,
            expected_revision=expected_revision,
            message="Job parsed aggregate could not be persisted",
        )
        return _log_outcome(job_id, expected_revision, outcome)
    outcome = JobParseTaskOutcome.PARSED if persisted else JobParseTaskOutcome.DISCARDED
    return _log_outcome(job_id, expected_revision, outcome)
