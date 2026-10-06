from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class SkillImportance(StrEnum):
    MANDATORY = "MANDATORY"
    OPTIONAL = "OPTIONAL"


class SkillEvidenceStatus(StrEnum):
    MATCHED = "MATCHED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"


class SkillGapSeverity(StrEnum):
    CRITICAL = "CRITICAL"
    MINOR = "MINOR"


@dataclass(frozen=True)
class CandidateSkill:
    skill_id: int
    name: str
    years_of_experience: Decimal | None = None


@dataclass(frozen=True)
class JobSkillRequirement:
    skill_id: int
    name: str
    importance: SkillImportance
    min_years_required: Decimal


@dataclass(frozen=True)
class CandidateExperience:
    start_date: date | None
    end_date: date | None
    is_current: bool


@dataclass(frozen=True)
class MatchWeights:
    skill: Decimal
    semantic: Decimal
    experience: Decimal


@dataclass(frozen=True)
class EmbeddingInput:
    resume_vector: Sequence[float] | None
    job_vector: Sequence[float] | None
    resume_model: str
    job_model: str
    resume_preprocessing_version: str
    job_preprocessing_version: str


@dataclass(frozen=True)
class SkillEvidence:
    skill_id: int
    name: str
    importance: SkillImportance
    status: SkillEvidenceStatus
    candidate_years: Decimal | None
    required_years: Decimal
    severity: SkillGapSeverity | None = None


@dataclass(frozen=True)
class MatchComputationResult:
    overall_score: Decimal
    skill_score: Decimal
    semantic_score: Decimal
    experience_score: Decimal
    algorithm_version: str
    matched_skills: tuple[SkillEvidence, ...]
    missing_skills: tuple[SkillEvidence, ...]
