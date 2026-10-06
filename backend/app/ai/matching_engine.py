from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from app.ai.matching_schemas import (
    CandidateExperience,
    CandidateSkill,
    EmbeddingInput,
    JobSkillRequirement,
    MatchComputationResult,
    MatchWeights,
    SkillEvidence,
    SkillEvidenceStatus,
    SkillGapSeverity,
    SkillImportance,
)

ALGORITHM_VERSION = "hybrid-v1"
EMBEDDING_DIMENSION = 1024
_HUNDRED = Decimal("100")
_DAYS_PER_YEAR = Decimal("365.25")
_SCORE_QUANTUM = Decimal("0.01")


class MatchComputationErrorKind(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    INCOMPATIBLE_EMBEDDINGS = "INCOMPATIBLE_EMBEDDINGS"
    INVALID_WEIGHTS = "INVALID_WEIGHTS"
    INVALID_SKILLS = "INVALID_SKILLS"


class MatchComputationError(Exception):
    """Controlled matching failure without scoring inputs or sensitive data."""

    def __init__(self, kind: MatchComputationErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _score(value: Decimal) -> Decimal:
    bounded = min(_HUNDRED, max(Decimal(0), value))
    return bounded.quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_UP)


def _nonnegative_decimal(
    value: Decimal,
    *,
    kind: MatchComputationErrorKind,
    label: str,
) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise MatchComputationError(kind, f"{label} must be a finite non-negative Decimal")
    return value


def _validate_skill_identity(skill_id: int, name: str) -> None:
    if isinstance(skill_id, bool) or not isinstance(skill_id, int) or skill_id <= 0:
        raise MatchComputationError(
            MatchComputationErrorKind.INVALID_SKILLS,
            "Skill IDs must be positive integers",
        )
    if not isinstance(name, str) or not name.strip():
        raise MatchComputationError(
            MatchComputationErrorKind.INVALID_SKILLS,
            "Skill names must be nonblank",
        )


def _importance(value: object) -> SkillImportance:
    if not isinstance(value, str):
        raise MatchComputationError(
            MatchComputationErrorKind.INVALID_SKILLS,
            "Skill importance must be MANDATORY or OPTIONAL",
        )
    try:
        return SkillImportance(value)
    except (TypeError, ValueError) as error:
        raise MatchComputationError(
            MatchComputationErrorKind.INVALID_SKILLS,
            "Skill importance must be MANDATORY or OPTIONAL",
        ) from error


def _skill_component(
    candidate_skills: Sequence[CandidateSkill],
    job_skills: Sequence[JobSkillRequirement],
) -> tuple[Decimal, tuple[SkillEvidence, ...], tuple[SkillEvidence, ...]]:
    if not job_skills:
        raise MatchComputationError(
            MatchComputationErrorKind.INVALID_SKILLS,
            "At least one Job skill is required",
        )

    candidate_by_id: dict[int, CandidateSkill] = {}
    for candidate_skill in candidate_skills:
        _validate_skill_identity(candidate_skill.skill_id, candidate_skill.name)
        if candidate_skill.skill_id in candidate_by_id:
            raise MatchComputationError(
                MatchComputationErrorKind.INVALID_SKILLS,
                "Candidate skill IDs must be unique",
            )
        if candidate_skill.years_of_experience is not None:
            _nonnegative_decimal(
                candidate_skill.years_of_experience,
                kind=MatchComputationErrorKind.INVALID_SKILLS,
                label="Candidate skill years",
            )
        candidate_by_id[candidate_skill.skill_id] = candidate_skill

    normalized_jobs: list[tuple[JobSkillRequirement, SkillImportance]] = []
    job_ids: set[int] = set()
    for requirement in job_skills:
        _validate_skill_identity(requirement.skill_id, requirement.name)
        if requirement.skill_id in job_ids:
            raise MatchComputationError(
                MatchComputationErrorKind.INVALID_SKILLS,
                "Job skill IDs must be unique",
            )
        job_ids.add(requirement.skill_id)
        importance = _importance(requirement.importance)
        _nonnegative_decimal(
            requirement.min_years_required,
            kind=MatchComputationErrorKind.INVALID_SKILLS,
            label="Required skill years",
        )
        normalized_jobs.append((requirement, importance))

    matched_weight = 0
    total_weight = 0
    matched: list[SkillEvidence] = []
    missing: list[SkillEvidence] = []
    for requirement, importance in sorted(normalized_jobs, key=lambda item: item[0].skill_id):
        weight = 2 if importance is SkillImportance.MANDATORY else 1
        total_weight += weight
        candidate = candidate_by_id.get(requirement.skill_id)
        if candidate is None:
            missing.append(
                SkillEvidence(
                    skill_id=requirement.skill_id,
                    name=requirement.name,
                    importance=importance,
                    status=SkillEvidenceStatus.MISSING,
                    candidate_years=None,
                    required_years=requirement.min_years_required,
                    severity=(
                        SkillGapSeverity.CRITICAL
                        if importance is SkillImportance.MANDATORY
                        else SkillGapSeverity.MINOR
                    ),
                )
            )
            continue

        matched_weight += weight
        candidate_years = candidate.years_of_experience
        status = SkillEvidenceStatus.MATCHED
        if requirement.min_years_required > 0 and (
            candidate_years is None or candidate_years < requirement.min_years_required
        ):
            status = SkillEvidenceStatus.PARTIAL
        matched.append(
            SkillEvidence(
                skill_id=requirement.skill_id,
                name=requirement.name,
                importance=importance,
                status=status,
                candidate_years=candidate_years,
                required_years=requirement.min_years_required,
            )
        )

    raw_score = _HUNDRED * Decimal(matched_weight) / Decimal(total_weight)
    return raw_score, tuple(matched), tuple(missing)


def _numeric_vector(values: Sequence[float] | None, label: str) -> tuple[float, ...]:
    if values is None or isinstance(values, (str, bytes)) or len(values) != EMBEDDING_DIMENSION:
        raise MatchComputationError(
            MatchComputationErrorKind.INCOMPATIBLE_EMBEDDINGS,
            f"{label} embedding must contain exactly {EMBEDDING_DIMENSION} values",
        )

    converted: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise MatchComputationError(
                MatchComputationErrorKind.INVALID_INPUT,
                f"{label} embedding values must be numeric and finite",
            )
        try:
            number = float(value)
        except (OverflowError, ValueError) as error:
            raise MatchComputationError(
                MatchComputationErrorKind.INVALID_INPUT,
                f"{label} embedding values must be numeric and finite",
            ) from error
        if not math.isfinite(number):
            raise MatchComputationError(
                MatchComputationErrorKind.INVALID_INPUT,
                f"{label} embedding values must be numeric and finite",
            )
        converted.append(number)
    return tuple(converted)


def _semantic_component(embeddings: EmbeddingInput) -> Decimal:
    for label, value in (
        ("Resume embedding model", embeddings.resume_model),
        ("Job embedding model", embeddings.job_model),
        ("Resume preprocessing version", embeddings.resume_preprocessing_version),
        ("Job preprocessing version", embeddings.job_preprocessing_version),
    ):
        if not isinstance(value, str) or not value.strip():
            raise MatchComputationError(
                MatchComputationErrorKind.INCOMPATIBLE_EMBEDDINGS,
                f"{label} must be nonblank",
            )
    if embeddings.resume_model != embeddings.job_model:
        raise MatchComputationError(
            MatchComputationErrorKind.INCOMPATIBLE_EMBEDDINGS,
            "Embedding models do not match",
        )
    if embeddings.resume_preprocessing_version != embeddings.job_preprocessing_version:
        raise MatchComputationError(
            MatchComputationErrorKind.INCOMPATIBLE_EMBEDDINGS,
            "Embedding preprocessing versions do not match",
        )

    resume = _numeric_vector(embeddings.resume_vector, "Resume")
    job = _numeric_vector(embeddings.job_vector, "Job")
    resume_scale = max(abs(value) for value in resume)
    job_scale = max(abs(value) for value in job)
    if resume_scale == 0 or job_scale == 0:
        raise MatchComputationError(
            MatchComputationErrorKind.INVALID_INPUT,
            "Embedding vectors must have non-zero norms",
        )
    scaled_resume = tuple(value / resume_scale for value in resume)
    scaled_job = tuple(value / job_scale for value in job)
    resume_norm = math.sqrt(math.fsum(value * value for value in scaled_resume))
    job_norm = math.sqrt(math.fsum(value * value for value in scaled_job))
    cosine = math.fsum(
        (left / resume_norm) * (right / job_norm)
        for left, right in zip(scaled_resume, scaled_job, strict=True)
    )
    clamped = min(1.0, max(0.0, cosine))
    return _HUNDRED * Decimal(str(clamped))


def _experience_component(
    experiences: Sequence[CandidateExperience],
    min_experience_years: Decimal,
    as_of_date: date,
) -> Decimal:
    required = _nonnegative_decimal(
        min_experience_years,
        kind=MatchComputationErrorKind.INVALID_INPUT,
        label="Minimum experience years",
    )
    if not isinstance(as_of_date, date) or isinstance(as_of_date, datetime):
        raise MatchComputationError(
            MatchComputationErrorKind.INVALID_INPUT,
            "as_of_date must be a date",
        )

    intervals: list[tuple[date, date]] = []
    for experience in experiences:
        start = experience.start_date
        if start is None:
            continue
        if not isinstance(start, date) or isinstance(start, datetime):
            raise MatchComputationError(
                MatchComputationErrorKind.INVALID_INPUT,
                "Experience dates must be date values",
            )
        end = as_of_date if experience.is_current else experience.end_date
        if end is None:
            continue
        if not isinstance(end, date) or isinstance(end, datetime):
            raise MatchComputationError(
                MatchComputationErrorKind.INVALID_INPUT,
                "Experience dates must be date values",
            )
        if end < start:
            continue
        intervals.append((start, end))

    if required == 0:
        return _HUNDRED
    if not intervals:
        return Decimal(0)

    intervals.sort()
    merged: list[tuple[date, date]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))

    total_days = sum((end - start).days for start, end in merged)
    candidate_years = Decimal(total_days) / _DAYS_PER_YEAR
    if candidate_years >= required:
        return _HUNDRED
    return _HUNDRED * candidate_years / required


def _validate_weights(weights: MatchWeights) -> None:
    values = (
        ("Skill weight", weights.skill),
        ("Semantic weight", weights.semantic),
        ("Experience weight", weights.experience),
    )
    for label, value in values:
        _nonnegative_decimal(
            value,
            kind=MatchComputationErrorKind.INVALID_WEIGHTS,
            label=label,
        )
        if value > 1:
            raise MatchComputationError(
                MatchComputationErrorKind.INVALID_WEIGHTS,
                f"{label} must not exceed 1",
            )
    if sum((value for _, value in values), Decimal(0)) != Decimal(1):
        raise MatchComputationError(
            MatchComputationErrorKind.INVALID_WEIGHTS,
            "Match weights must total exactly 1",
        )


def compute_match(
    *,
    candidate_skills: Sequence[CandidateSkill],
    job_skills: Sequence[JobSkillRequirement],
    candidate_experiences: Sequence[CandidateExperience],
    min_experience_years: Decimal,
    weights: MatchWeights,
    embeddings: EmbeddingInput,
    as_of_date: date,
) -> MatchComputationResult:
    """Compute a deterministic hybrid-v1 result from already-loaded scoring data."""

    _validate_weights(weights)
    raw_skill, matched_skills, missing_skills = _skill_component(
        candidate_skills,
        job_skills,
    )
    raw_semantic = _semantic_component(embeddings)
    raw_experience = _experience_component(
        candidate_experiences,
        min_experience_years,
        as_of_date,
    )
    raw_overall = (
        weights.skill * raw_skill
        + weights.semantic * raw_semantic
        + weights.experience * raw_experience
    )
    return MatchComputationResult(
        overall_score=_score(raw_overall),
        skill_score=_score(raw_skill),
        semantic_score=_score(raw_semantic),
        experience_score=_score(raw_experience),
        algorithm_version=ALGORITHM_VERSION,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
    )
