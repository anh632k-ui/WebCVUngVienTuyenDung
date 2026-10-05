from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class TaxonomySkill:
    id: int
    name: str
    normalized_name: str
    skill_kind: Literal["HARD", "SOFT"]
    category: str


@dataclass(frozen=True)
class ParsedProfile:
    full_name: str | None = None
    email: str | None = None
    phone_number: str | None = None
    current_title: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    professional_summary: str | None = None


@dataclass(frozen=True)
class ParsedSkill:
    skill_id: int
    name: str
    normalized_name: str
    years_of_experience: Decimal | None = None
    proficiency_level: str | None = None


@dataclass(frozen=True)
class ParsedExperience:
    company_name: str
    job_title: str
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    description: str | None = None


@dataclass(frozen=True)
class ParsedEducation:
    institution_name: str
    degree: str | None = None
    field_of_study: str | None = None
    start_year: int | None = None
    graduation_year: int | None = None
    gpa: Decimal | None = None
    description: str | None = None


@dataclass(frozen=True)
class ParsedResumeResult:
    raw_text: str
    profile: ParsedProfile = field(default_factory=ParsedProfile)
    skills: tuple[ParsedSkill, ...] = ()
    experiences: tuple[ParsedExperience, ...] = ()
    educations: tuple[ParsedEducation, ...] = ()
