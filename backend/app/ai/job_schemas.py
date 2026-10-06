from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

JobSkillImportance = Literal["MANDATORY", "OPTIONAL"]


@dataclass(frozen=True)
class ParsedJobSkill:
    skill_id: int
    name: str
    normalized_name: str
    importance: JobSkillImportance
    min_years_required: Decimal


@dataclass(frozen=True)
class ParsedJobResult:
    normalized_text: str
    min_experience_years: Decimal
    education_requirement: str | None
    skills: tuple[ParsedJobSkill, ...]
