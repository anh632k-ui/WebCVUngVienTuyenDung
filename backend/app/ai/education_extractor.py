from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.ai.schemas import ParsedEducation

_INSTITUTION_WORDS = (
    "university",
    "college",
    "institute",
    "academy",
    "school",
    "đại học",
    "cao đẳng",
    "học viện",
    "trường",
)
_YEAR_RANGE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*(?:-|–|—|to|đến)\s*((?:19|20)\d{2})(?!\d)", re.I
)
_GPA = re.compile(r"\bGPA\s*:\s*(\d+(?:\.\d+)?)(?:\s*/\s*\d+(?:\.\d+)?)?", re.I)
_DEGREE_LABEL = re.compile(r"^(?:degree|bằng cấp)\s*:\s*(.+)$", re.I)
_FIELD_LABEL = re.compile(r"^(?:field|major|chuyên ngành)\s*:\s*(.+)$", re.I)
_DEGREE_FIELD = re.compile(
    r"^(Bachelor(?: of [A-Za-z ]+)?|Master(?: of [A-Za-z ]+)?|BSc|MSc|Cử nhân|Thạc sĩ)"
    r"(?:\s+(?:in|ngành)\s+(.+))?$",
    re.I,
)


def _blocks(text: str) -> list[list[str]]:
    return [
        [line.strip() for line in block.splitlines() if line.strip()]
        for block in re.split(r"\n\s*\n", text)
        if block.strip()
    ]


def _institution(line: str) -> str | None:
    lowered = line.casefold()
    if any(word in lowered for word in _INSTITUTION_WORDS) and len(line) <= 150:
        return line
    return None


def _parse_gpa(line: str) -> Decimal | None:
    match = _GPA.search(line)
    if not match:
        return None
    try:
        value = Decimal(match.group(1))
    except InvalidOperation:
        return None
    return value if Decimal("0") <= value <= Decimal("100") else None


def extract_educations(section_text: str) -> tuple[ParsedEducation, ...]:
    results: list[ParsedEducation] = []
    for block in _blocks(section_text):
        institution = _institution(block[0]) if block else None
        if institution is None:
            continue
        degree: str | None = None
        field_of_study: str | None = None
        start_year: int | None = None
        graduation_year: int | None = None
        gpa: Decimal | None = None
        for line in block[1:]:
            degree_label = _DEGREE_LABEL.fullmatch(line)
            field_label = _FIELD_LABEL.fullmatch(line)
            degree_field = _DEGREE_FIELD.fullmatch(line)
            year_range = _YEAR_RANGE.search(line)
            if degree_label:
                degree = degree_label.group(1).strip()[:100] or None
            elif field_label:
                field_of_study = field_label.group(1).strip()[:150] or None
            elif degree_field:
                degree = degree_field.group(1).strip()[:100]
                field_of_study = (
                    degree_field.group(2).strip()[:150] if degree_field.group(2) else None
                )
            if year_range:
                candidate_start = int(year_range.group(1))
                candidate_end = int(year_range.group(2))
                if candidate_end >= candidate_start:
                    start_year = candidate_start
                    graduation_year = candidate_end
            if gpa is None:
                gpa = _parse_gpa(line)
        results.append(
            ParsedEducation(
                institution_name=institution,
                degree=degree,
                field_of_study=field_of_study,
                start_year=start_year,
                graduation_year=graduation_year,
                gpa=gpa,
            )
        )
    return tuple(results)
