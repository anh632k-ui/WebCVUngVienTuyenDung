from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, cast

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.errors import ResumeParseError
from app.ai.resume_parser import RESUME_TEXT_PREPROCESSING_VERSION, parse_resume
from app.ai.schemas import ParsedProfile, ParsedResumeResult, TaxonomySkill
from app.ai.vector_embedding import (
    BGE_M3_EMBEDDING_DIMENSION,
    BGE_M3_MODEL_NAME,
    EmbeddingProvider,
    EmbeddingProviderError,
    validate_dense_embedding,
)
from app.core.version_guard import claim_resume_revision, fail_resume_revision
from app.models.resume import CandidateProfile, Resume, ResumeEducation, ResumeExperience
from app.models.skill import ResumeSkill, Skill
from app.storage.resume_storage import ResumeStorage

logger = logging.getLogger(__name__)

MAX_WORKER_ERROR_MESSAGE_LENGTH = 500

SessionFactory = Callable[[], AsyncSession]


class ResumeParseTaskOutcome(StrEnum):
    PARSED = "PARSED"
    FAILED = "FAILED"
    DISCARDED = "DISCARDED"


@dataclass(frozen=True)
class ResumeParseContext:
    storage_key: str
    mime_type: str
    taxonomy: tuple[TaxonomySkill, ...]


def _bounded_error_message(message: str) -> str:
    return message[:MAX_WORKER_ERROR_MESSAGE_LENGTH]


async def _load_parse_context(
    session_factory: SessionFactory,
    *,
    resume_id: uuid.UUID,
    expected_revision: int,
) -> ResumeParseContext | None:
    async with session_factory() as session:
        resume_row = (
            await session.execute(
                select(Resume.storage_key, Resume.mime_type).where(
                    Resume.id == resume_id,
                    Resume.revision == expected_revision,
                    Resume.parsing_status == "PROCESSING",
                    Resume.is_deleted.is_(False),
                )
            )
        ).one_or_none()
        if resume_row is None:
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
        return ResumeParseContext(resume_row.storage_key, resume_row.mime_type, taxonomy)


def _profile_values(profile: ParsedProfile) -> dict[str, str | None]:
    return {
        "full_name": profile.full_name,
        "email": profile.email,
        "phone_number": profile.phone_number,
        "current_title": profile.current_title,
        "location": profile.location,
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        "professional_summary": profile.professional_summary,
    }


def _has_meaningful_profile(profile_values: dict[str, str | None]) -> bool:
    return any(value is not None and value.strip() for value in profile_values.values())


def _validate_parsed_skill_ids(
    parsed: ParsedResumeResult,
    taxonomy: tuple[TaxonomySkill, ...],
) -> None:
    taxonomy_ids = {skill.id for skill in taxonomy}
    parsed_ids = [skill.skill_id for skill in parsed.skills]
    if len(parsed_ids) != len(set(parsed_ids)) or not set(parsed_ids).issubset(taxonomy_ids):
        raise RuntimeError("Parsed skills do not map uniquely to the canonical taxonomy")


async def _persist_success(
    session_factory: SessionFactory,
    *,
    resume_id: uuid.UUID,
    expected_revision: int,
    parsed: ParsedResumeResult,
    embedding: list[float],
) -> bool:
    now = datetime.now(UTC)
    async with session_factory() as session:
        try:
            result = await session.execute(
                update(Resume)
                .where(
                    Resume.id == resume_id,
                    Resume.revision == expected_revision,
                    Resume.parsing_status == "PROCESSING",
                    Resume.is_deleted.is_(False),
                )
                .values(
                    parsing_status="PARSED",
                    raw_text=parsed.raw_text,
                    resume_embedding=embedding,
                    embedding_model=BGE_M3_MODEL_NAME,
                    embedding_preprocessing_version=RESUME_TEXT_PREPROCESSING_VERSION,
                    parsed_at=now,
                    error_message=None,
                    updated_at=now,
                )
                .returning(Resume.id)
            )
            if result.scalar_one_or_none() is None:
                await session.rollback()
                return False

            await session.execute(
                delete(CandidateProfile).where(CandidateProfile.resume_id == resume_id)
            )
            await session.execute(delete(ResumeSkill).where(ResumeSkill.resume_id == resume_id))
            await session.execute(
                delete(ResumeExperience).where(ResumeExperience.resume_id == resume_id)
            )
            await session.execute(
                delete(ResumeEducation).where(ResumeEducation.resume_id == resume_id)
            )

            profile_values = _profile_values(parsed.profile)
            if _has_meaningful_profile(profile_values):
                session.add(CandidateProfile(resume_id=resume_id, **profile_values))
            session.add_all(
                ResumeSkill(
                    resume_id=resume_id,
                    skill_id=skill.skill_id,
                    years_of_experience=skill.years_of_experience,
                    proficiency_level=skill.proficiency_level,
                )
                for skill in parsed.skills
            )
            session.add_all(
                ResumeExperience(
                    resume_id=resume_id,
                    company_name=experience.company_name,
                    job_title=experience.job_title,
                    start_date=experience.start_date,
                    end_date=experience.end_date,
                    is_current=experience.is_current,
                    description=experience.description,
                )
                for experience in parsed.experiences
            )
            session.add_all(
                ResumeEducation(
                    resume_id=resume_id,
                    institution_name=education.institution_name,
                    degree=education.degree,
                    field_of_study=education.field_of_study,
                    start_year=education.start_year,
                    graduation_year=education.graduation_year,
                    gpa=education.gpa,
                    description=education.description,
                )
                for education in parsed.educations
            )
            await session.commit()
            return True
        except Exception:
            await session.rollback()
            raise


async def _mark_failed(
    session_factory: SessionFactory,
    *,
    resume_id: uuid.UUID,
    expected_revision: int,
    message: str,
) -> ResumeParseTaskOutcome:
    try:
        async with session_factory() as session:
            failed = await fail_resume_revision(
                session,
                resume_id=resume_id,
                expected_revision=expected_revision,
                error_message=_bounded_error_message(message),
            )
    except Exception:  # noqa: BLE001 - failure CAS is best-effort after a claimed task failure
        logger.error(
            "resume_parse_failure_cas_error resume_id=%s expected_revision=%s",
            resume_id,
            expected_revision,
        )
        return ResumeParseTaskOutcome.DISCARDED
    return ResumeParseTaskOutcome.FAILED if failed else ResumeParseTaskOutcome.DISCARDED


def _log_outcome(
    resume_id: uuid.UUID,
    expected_revision: int,
    outcome: ResumeParseTaskOutcome,
) -> ResumeParseTaskOutcome:
    logger.info(
        "resume_parse_task resume_id=%s expected_revision=%s outcome=%s",
        resume_id,
        expected_revision,
        outcome.value,
    )
    return outcome


async def process_resume_parse_task(
    resume_id: uuid.UUID,
    expected_revision: int,
    *,
    session_factory: SessionFactory,
    storage: ResumeStorage,
    embedding_provider: EmbeddingProvider,
) -> ResumeParseTaskOutcome:
    async with session_factory() as claim_session:
        claimed = await claim_resume_revision(
            claim_session,
            resume_id=resume_id,
            expected_revision=expected_revision,
        )
    if not claimed:
        return _log_outcome(resume_id, expected_revision, ResumeParseTaskOutcome.DISCARDED)

    try:
        context = await _load_parse_context(
            session_factory,
            resume_id=resume_id,
            expected_revision=expected_revision,
        )
    except Exception:  # noqa: BLE001 - taxonomy/metadata failures become controlled FAILED
        outcome = await _mark_failed(
            session_factory,
            resume_id=resume_id,
            expected_revision=expected_revision,
            message="Resume taxonomy or metadata could not be loaded",
        )
        return _log_outcome(resume_id, expected_revision, outcome)
    if context is None:
        return _log_outcome(resume_id, expected_revision, ResumeParseTaskOutcome.DISCARDED)

    try:
        source_bytes = await storage.read_bytes(context.storage_key)
    except Exception:  # noqa: BLE001 - storage details must not enter the persisted message
        outcome = await _mark_failed(
            session_factory,
            resume_id=resume_id,
            expected_revision=expected_revision,
            message="Resume source could not be read",
        )
        return _log_outcome(resume_id, expected_revision, outcome)

    try:
        parsed = await asyncio.to_thread(
            parse_resume,
            source_bytes,
            context.mime_type,
            context.taxonomy,
        )
        _validate_parsed_skill_ids(parsed, context.taxonomy)
    except ResumeParseError as error:
        outcome = await _mark_failed(
            session_factory,
            resume_id=resume_id,
            expected_revision=expected_revision,
            message=f"Resume parsing failed: {error.kind.value}",
        )
        return _log_outcome(resume_id, expected_revision, outcome)
    except Exception:  # noqa: BLE001 - parser internals must not enter the persisted message
        outcome = await _mark_failed(
            session_factory,
            resume_id=resume_id,
            expected_revision=expected_revision,
            message="Resume parsing failed",
        )
        return _log_outcome(resume_id, expected_revision, outcome)

    try:
        if (
            embedding_provider.model_name != BGE_M3_MODEL_NAME
            or embedding_provider.dimension != BGE_M3_EMBEDDING_DIMENSION
        ):
            raise EmbeddingProviderError("Embedding provider metadata is incompatible")
        embedding = validate_dense_embedding(
            await embedding_provider.embed(parsed.raw_text),
            expected_dimension=BGE_M3_EMBEDDING_DIMENSION,
        )
    except Exception:  # noqa: BLE001 - runtime details must not enter the persisted message
        outcome = await _mark_failed(
            session_factory,
            resume_id=resume_id,
            expected_revision=expected_revision,
            message="Resume embedding failed",
        )
        return _log_outcome(resume_id, expected_revision, outcome)

    try:
        persisted = await _persist_success(
            session_factory,
            resume_id=resume_id,
            expected_revision=expected_revision,
            parsed=parsed,
            embedding=embedding,
        )
    except Exception:  # noqa: BLE001 - persistence failures become controlled FAILED
        outcome = await _mark_failed(
            session_factory,
            resume_id=resume_id,
            expected_revision=expected_revision,
            message="Resume parsed aggregate could not be persisted",
        )
        return _log_outcome(resume_id, expected_revision, outcome)
    outcome = ResumeParseTaskOutcome.PARSED if persisted else ResumeParseTaskOutcome.DISCARDED
    return _log_outcome(resume_id, expected_revision, outcome)
