from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from app.ai.job_schemas import JobSkillImportance, ParsedJobResult, ParsedJobSkill
from app.ai.job_sections import ContextualJobLine, JobSection, contextual_job_lines
from app.ai.job_text_processing import normalize_job_text
from app.ai.schemas import TaxonomySkill
from app.ai.skill_normalizer import normalize_skills, taxonomy_skill_pattern

_ZERO_YEARS = Decimal("0.0")
_ONE_DECIMAL = Decimal("0.1")
_MAX_EXPERIENCE_YEARS = Decimal("99.9")
_NUMBER = r"\d{1,2}(?:[.,]\d)?"
_CAPTURE_YEARS = rf"(?<![\d.,+\-])(?P<years>{_NUMBER})(?![\d.,])"
_REQUIRED_RANGE_OR_PLUS = rf"(?:\s*(?:-|–|—|to|đến)\s*{_NUMBER}|\s*\+)"
_RANGE_OR_PLUS = rf"(?:\s*(?:-|–|—|to|đến)\s*{_NUMBER}|\s*\+)?"

_SENTENCE_BOUNDARY = re.compile(r"(?<=[!?;])\s+|(?<=\.)\s+(?=[A-ZÀ-Ỵ])")
_MANDATORY_CUE = re.compile(
    r"\b(?:required|must(?:\s+have)?|mandatory)\b|\bbắt\s+buộc\b|\bphải\s+có\b",
    re.IGNORECASE,
)
_OPTIONAL_CUE = re.compile(
    r"\b(?:preferred|nice\s+to\s+have|bonus|optional)\b|\bis\s+a\s+plus\b|"
    r"\bưu\s+tiên\b|\blà\s+lợi\s+thế\b|\bđiểm\s+cộng\b",
    re.IGNORECASE,
)

_GENERAL_EXPERIENCE_PATTERNS = (
    re.compile(
        rf"(?:\bminimum\s+|\bat\s+least\s+){_CAPTURE_YEARS}{_RANGE_OR_PLUS}\s*"
        r"years?\s+(?:of\s+)?(?:professional\s+|work\s+)?experience\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"{_CAPTURE_YEARS}{_REQUIRED_RANGE_OR_PLUS}\s*years?\s+(?:of\s+)?"
        r"(?:professional\s+|work\s+)?experience\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"{_CAPTURE_YEARS}\s*years?\s+(?:of\s+)?(?:professional\s+|work\s+)?"
        r"experience\s+(?:is\s+)?required\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:tối\s+thiểu|ít\s+nhất)\s+{_CAPTURE_YEARS}{_RANGE_OR_PLUS}\s*"
        r"năm\s+kinh\s+nghiệm\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bkinh\s+nghiệm\s+từ\s+{_CAPTURE_YEARS}{_RANGE_OR_PLUS}\s*năm\b",
        re.IGNORECASE,
    ),
)

_EDUCATION_EVIDENCE = re.compile(
    r"\b(?:bachelor(?:'s)?\s+degree|bachelor\s+of|bs/ba|bsc|university\s+degree|"
    r"degree\s+in|academic\s+degree)\b|\b(?:tốt\s+nghiệp\s+đại\s+học|cử\s+nhân|"
    r"trình\s+độ\s*:\s*đại\s+học|bằng\s+đại\s+học)\b",
    re.IGNORECASE,
)


def _decimal_years(raw_value: str) -> Decimal | None:
    try:
        value = Decimal(raw_value.replace(",", ".")).quantize(_ONE_DECIMAL)
    except InvalidOperation:
        return None
    if _ZERO_YEARS <= value <= _MAX_EXPERIENCE_YEARS:
        return value
    return None


def _sentences(line: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _SENTENCE_BOUNDARY.split(line) if part.strip())


def _importance(sentence: str, section: JobSection | None) -> JobSkillImportance:
    # Explicit mandatory evidence wins even when the same skill is also preferred.
    if section is JobSection.MANDATORY or _MANDATORY_CUE.search(sentence):
        return "MANDATORY"
    if section is JobSection.OPTIONAL or _OPTIONAL_CUE.search(sentence):
        return "OPTIONAL"
    return "OPTIONAL"


def _skill_years(sentence: str, skill: TaxonomySkill) -> Decimal:
    skill_source = taxonomy_skill_pattern(skill).pattern
    patterns = (
        re.compile(
            rf"{skill_source}\s*(?::|-|–|—)?\s*(?:minimum\s+|at\s+least\s+)?"
            rf"{_CAPTURE_YEARS}{_RANGE_OR_PLUS}\s*years?"
            r"(?:\s+(?:of\s+)?experience)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?:minimum\s+|at\s+least\s+)?{_CAPTURE_YEARS}{_RANGE_OR_PLUS}\s*"
            rf"years?\s+(?:(?:of\s+)?experience\s+)?(?:with|using|in|of)?\s*{skill_source}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?:tối\s+thiểu|ít\s+nhất)\s+{_CAPTURE_YEARS}{_RANGE_OR_PLUS}\s*"
            rf"năm(?:\s+kinh\s+nghiệm)?(?:\s+(?:với|về))?\s*{skill_source}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"{skill_source}\s*(?::|-|–|—)?\s*(?:tối\s+thiểu|ít\s+nhất)?\s*"
            rf"{_CAPTURE_YEARS}{_RANGE_OR_PLUS}\s*năm(?:\s+kinh\s+nghiệm)?\b",
            re.IGNORECASE,
        ),
    )
    values = [
        value
        for pattern in patterns
        if (match := pattern.search(sentence)) is not None
        if (value := _decimal_years(match.group("years"))) is not None
    ]
    return max(values, default=_ZERO_YEARS)


def _extract_skills(
    lines: Sequence[ContextualJobLine],
    taxonomy: Sequence[TaxonomySkill],
) -> tuple[ParsedJobSkill, ...]:
    taxonomy_by_id = {skill.id: skill for skill in taxonomy}
    evidence: dict[int, tuple[JobSkillImportance, Decimal]] = {}
    for line in lines:
        for sentence in _sentences(line.text):
            for matched in normalize_skills(sentence, taxonomy):
                skill = taxonomy_by_id[matched.skill_id]
                importance = _importance(sentence, line.section)
                years = _skill_years(sentence, skill)
                previous = evidence.get(skill.id)
                if previous is not None:
                    if previous[0] == "MANDATORY":
                        importance = "MANDATORY"
                    years = max(years, previous[1])
                evidence[skill.id] = (importance, years)

    return tuple(
        ParsedJobSkill(
            skill_id=skill_id,
            name=taxonomy_by_id[skill_id].name,
            normalized_name=taxonomy_by_id[skill_id].normalized_name,
            importance=importance,
            min_years_required=years,
        )
        for skill_id, (importance, years) in sorted(evidence.items())
    )


def _extract_overall_experience(
    lines: Sequence[ContextualJobLine],
    taxonomy: Sequence[TaxonomySkill],
) -> Decimal:
    requirements: list[Decimal] = []
    for line in lines:
        for sentence in _sentences(line.text):
            # A sentence naming a taxonomy skill is treated as skill-specific, not global.
            if normalize_skills(sentence, taxonomy):
                continue
            for pattern in _GENERAL_EXPERIENCE_PATTERNS:
                match = pattern.search(sentence)
                if match and (value := _decimal_years(match.group("years"))) is not None:
                    requirements.append(value)
    # Multiple global minima are combined conservatively by retaining the strongest evidence.
    return max(requirements, default=_ZERO_YEARS)


def _education_candidate(line: str) -> str | None:
    candidate = line.strip().lstrip("-*• ").strip()
    if not _EDUCATION_EVIDENCE.search(candidate):
        return None
    if len(candidate) <= 255:
        return candidate
    for sentence in _sentences(candidate):
        if len(sentence) <= 255 and _EDUCATION_EVIDENCE.search(sentence):
            return sentence
    return None


def _extract_education(lines: Sequence[ContextualJobLine]) -> str | None:
    for line in lines:
        if candidate := _education_candidate(line.text):
            return candidate
    return None


def parse_job_description(
    raw_content: str,
    taxonomy: Sequence[TaxonomySkill],
) -> ParsedJobResult:
    """Extract deterministic, evidence-only Job criteria without external side effects."""
    raw_text = normalize_job_text(raw_content)
    lines = contextual_job_lines(raw_text)
    return ParsedJobResult(
        raw_text=raw_text,
        min_experience_years=_extract_overall_experience(lines, taxonomy),
        education_requirement=_extract_education(lines),
        skills=_extract_skills(lines, taxonomy),
    )
