from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from app.ai.job_schemas import JobSkillImportance, ParsedJobResult, ParsedJobSkill
from app.ai.job_sections import ContextualJobLine, JobSection, contextual_job_lines
from app.ai.schemas import TaxonomySkill
from app.ai.skill_normalizer import taxonomy_skill_pattern
from app.ai.text_processing import normalize_parser_text

MAX_JOB_TEXT_CHARS = 1_000_000

_ZERO_YEARS = Decimal("0.0")
_ONE_DECIMAL = Decimal("0.1")
_MAX_YEARS = Decimal("99.9")
_NUMBER = r"\d{1,2}(?:[.,]\d)?"
_CAPTURE_YEARS = rf"(?<![\d.,+\-])(?P<years>{_NUMBER})(?![\d.,])"
_RANGE_OR_PLUS = rf"(?:\s*(?:-|–|—|to|đến)\s*{_NUMBER}|\s*\+)?"
_REQUIRED_RANGE_OR_PLUS = rf"(?:\s*(?:-|–|—|to|đến)\s*{_NUMBER}|\s*\+)"

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?;])\s+")
_MANDATORY_CUE = re.compile(
    r"\b(?:required|must(?:\s+have)?|mandatory|essential|minimum\s+qualification)\b|"
    r"(?<!không\s)\bbắt\s+buộc\b|\byêu\s+cầu\b|\btối\s+thiểu\b",
    re.IGNORECASE,
)
_OPTIONAL_CUE = re.compile(
    r"\b(?:preferred|nice\s+to\s+have|bonus|advantage|optional|plus)\b|"
    r"\bưu\s+tiên\b|\blợi\s+thế\b|\bđiểm\s+cộng\b|\bkhông\s+bắt\s+buộc\b",
    re.IGNORECASE,
)
_NON_CANDIDATE_EXPERIENCE = re.compile(
    r"\b(?:company|organization|business|project|product)\b|"
    r"\b(?:công\s+ty|doanh\s+nghiệp|dự\s+án|sản\s+phẩm)\b",
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
        rf"\b(?:tối\s+thiểu|ít\s+nhất|yêu\s+cầu)\s+{_CAPTURE_YEARS}"
        rf"{_RANGE_OR_PLUS}\s*năm\s+kinh\s+nghiệm\b",
        re.IGNORECASE,
    ),
)

_EDUCATION_EVIDENCE = re.compile(
    r"\b(?:bachelor(?:'s)?\s+degree|bachelor\s+degree|bachelor\s+of|"
    r"master(?:'s)?\s+degree|bs/ba|bsc|university\s+degree|degree\s+or\s+equivalent|"
    r"degree\s+in)\b|\b(?:tốt\s+nghiệp\s+đại\s+học|bằng\s+cử\s+nhân|cử\s+nhân|"
    r"đại\s+học\s+ngành|cao\s+đẳng\s+trở\s+lên)\b",
    re.IGNORECASE,
)


class JobParseErrorKind(StrEnum):
    NO_MEANINGFUL_TEXT = "NO_MEANINGFUL_TEXT"
    TEXT_LIMIT_EXCEEDED = "TEXT_LIMIT_EXCEEDED"
    INVALID_INPUT = "INVALID_INPUT"


class JobParseError(Exception):
    """Controlled deterministic parser failure without infrastructure details."""

    def __init__(self, kind: JobParseErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _normalize_job_text(raw_content: str) -> str:
    if not isinstance(raw_content, str):
        raise JobParseError(JobParseErrorKind.INVALID_INPUT, "Job content must be text")
    try:
        raw_content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise JobParseError(
            JobParseErrorKind.INVALID_INPUT, "Job content is invalid Unicode"
        ) from error
    normalized = normalize_parser_text(raw_content)
    if len(normalized) > MAX_JOB_TEXT_CHARS:
        raise JobParseError(
            JobParseErrorKind.TEXT_LIMIT_EXCEEDED,
            "Normalized Job content exceeds the parser limit",
        )
    if not any(character.isalnum() for character in normalized):
        raise JobParseError(
            JobParseErrorKind.NO_MEANINGFUL_TEXT,
            "Job content contains no meaningful alphanumeric text",
        )
    return normalized


def _decimal_years(raw_value: str) -> Decimal | None:
    try:
        value = Decimal(raw_value.replace(",", ".")).quantize(_ONE_DECIMAL)
    except InvalidOperation:
        return None
    return value if _ZERO_YEARS <= value <= _MAX_YEARS else None


def _sentences(line: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _SENTENCE_BOUNDARY.split(line) if part.strip())


def _ordered_taxonomy(
    taxonomy: Sequence[TaxonomySkill],
) -> tuple[TaxonomySkill, ...]:
    unique: dict[int, TaxonomySkill] = {}
    for skill in sorted(taxonomy, key=lambda item: item.id):
        unique.setdefault(skill.id, skill)
    return tuple(unique.values())


def _skill_years(sentence: str, skill_pattern: re.Pattern[str]) -> Decimal:
    skill_source = skill_pattern.pattern
    patterns = (
        re.compile(
            rf"{skill_source}\s*(?::|-|–|—)?\s*(?:minimum\s+|at\s+least\s+)?"
            rf"{_CAPTURE_YEARS}{_RANGE_OR_PLUS}\s*years?"
            r"(?:\s+(?:of\s+)?experience)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?:minimum\s+|at\s+least\s+)?{_CAPTURE_YEARS}{_RANGE_OR_PLUS}\s*years?\s+"
            rf"(?:(?:of\s+)?experience\s+)?(?:with|using|in|of)?\s*{skill_source}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?:tối\s+thiểu|ít\s+nhất|yêu\s+cầu)\s+{_CAPTURE_YEARS}"
            rf"{_RANGE_OR_PLUS}\s*năm(?:\s+kinh\s+nghiệm)?(?:\s+(?:với|về))?\s*"
            rf"{skill_source}",
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


def _importance(
    sentence: str,
    section: JobSection | None,
    skill_years: Decimal,
    skill_pattern: re.Pattern[str],
    skill_count: int,
) -> JobSkillImportance | None:
    skill_match = skill_pattern.search(sentence)
    mandatory_matches = tuple(_MANDATORY_CUE.finditer(sentence))
    optional_matches = tuple(_OPTIONAL_CUE.finditer(sentence))
    if skill_match is not None and mandatory_matches and optional_matches:
        if skill_count == 1:
            return "MANDATORY"

        def distance(cue: re.Match[str]) -> int:
            if cue.end() <= skill_match.start():
                return skill_match.start() - cue.end()
            if skill_match.end() <= cue.start():
                return cue.start() - skill_match.end()
            return 0

        mandatory_distance = min(distance(cue) for cue in mandatory_matches)
        optional_distance = min(distance(cue) for cue in optional_matches)
        return "MANDATORY" if mandatory_distance <= optional_distance else "OPTIONAL"
    if mandatory_matches:
        return "MANDATORY"
    if optional_matches:
        return "OPTIONAL"
    if section is JobSection.MANDATORY:
        return "MANDATORY"
    if section is JobSection.OPTIONAL:
        return "OPTIONAL"
    if skill_years > _ZERO_YEARS:
        return "MANDATORY"
    return None


def _extract_skills(
    lines: Sequence[ContextualJobLine],
    taxonomy: Sequence[TaxonomySkill],
) -> tuple[ParsedJobSkill, ...]:
    ordered = _ordered_taxonomy(taxonomy)
    patterns = {skill.id: taxonomy_skill_pattern(skill) for skill in ordered}
    by_id = {skill.id: skill for skill in ordered}
    evidence: dict[int, tuple[JobSkillImportance, Decimal]] = {}
    for line in lines:
        for sentence in _sentences(line.text):
            matched = tuple(skill for skill in ordered if patterns[skill.id].search(sentence))
            for skill in matched:
                pattern = patterns[skill.id]
                years = _skill_years(sentence, pattern)
                importance = _importance(sentence, line.section, years, pattern, len(matched))
                if importance is None:
                    continue
                previous = evidence.get(skill.id)
                if previous is not None:
                    if previous[0] == "MANDATORY":
                        importance = "MANDATORY"
                    years = max(years, previous[1])
                evidence[skill.id] = (importance, years)

    return tuple(
        ParsedJobSkill(
            skill_id=skill_id,
            name=by_id[skill_id].name,
            normalized_name=by_id[skill_id].normalized_name,
            importance=importance,
            min_years_required=years,
        )
        for skill_id, (importance, years) in sorted(evidence.items())
    )


def _extract_global_experience(
    lines: Sequence[ContextualJobLine],
    taxonomy: Sequence[TaxonomySkill],
) -> Decimal:
    skill_patterns = tuple(taxonomy_skill_pattern(skill) for skill in _ordered_taxonomy(taxonomy))
    requirements: list[Decimal] = []
    for line in lines:
        for sentence in _sentences(line.text):
            if _NON_CANDIDATE_EXPERIENCE.search(sentence) or any(
                pattern.search(sentence) for pattern in skill_patterns
            ):
                continue
            for pattern in _GENERAL_EXPERIENCE_PATTERNS:
                match = pattern.search(sentence)
                if match and (value := _decimal_years(match.group("years"))) is not None:
                    requirements.append(value)
    return max(requirements, default=_ZERO_YEARS)


def _education_candidate(line: str) -> str | None:
    candidate = line.strip().lstrip("-*• ").strip()
    if _EDUCATION_EVIDENCE.search(candidate) is None:
        return None
    if len(candidate) <= 255:
        return candidate
    for sentence in _sentences(candidate):
        if len(sentence) <= 255 and _EDUCATION_EVIDENCE.search(sentence):
            return sentence
    evidence = _EDUCATION_EVIDENCE.search(candidate)
    return evidence.group(0) if evidence is not None and len(evidence.group(0)) <= 255 else None


def _extract_education(lines: Sequence[ContextualJobLine]) -> str | None:
    for line in lines:
        if candidate := _education_candidate(line.text):
            return candidate
    return None


def parse_job_description(
    raw_content: str,
    taxonomy: Sequence[TaxonomySkill],
) -> ParsedJobResult:
    """Return evidence-only Job criteria without I/O, persistence, or model inference."""
    normalized_text = _normalize_job_text(raw_content)
    lines = contextual_job_lines(normalized_text)
    return ParsedJobResult(
        normalized_text=normalized_text,
        min_experience_years=_extract_global_experience(lines, taxonomy),
        education_requirement=_extract_education(lines),
        skills=_extract_skills(lines, taxonomy),
    )
