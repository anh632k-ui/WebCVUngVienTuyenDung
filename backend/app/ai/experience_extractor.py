from __future__ import annotations

import re
from datetime import date

from app.ai.schemas import ParsedExperience

_CURRENT_MARKERS = {"present", "current", "now", "hiện tại", "nay"}
_DATE_TOKEN = (
    r"(?:0?[1-9]|1[0-2])/\d{4}|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+\d{4}|\d{4}"
)
_RANGE = re.compile(
    rf"^\s*({_DATE_TOKEN})\s*(?:-|–|—|to|đến)\s*"
    rf"({_DATE_TOKEN}|present|current|now|hiện tại|nay)\s*$",
    re.I,
)
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _blocks(text: str) -> list[list[str]]:
    return [
        [line.strip() for line in block.splitlines() if line.strip()]
        for block in re.split(r"\n\s*\n", text)
        if block.strip()
    ]


def _parse_month_date(value: str) -> date | None:
    value = value.strip().casefold()
    numeric = re.fullmatch(r"(0?[1-9]|1[0-2])/(\d{4})", value)
    if numeric:
        return date(int(numeric.group(2)), int(numeric.group(1)), 1)
    named = re.fullmatch(r"([a-z]+)\s+(\d{4})", value)
    if named and named.group(1) in _MONTHS:
        return date(int(named.group(2)), _MONTHS[named.group(1)], 1)
    # Year-only evidence is retained in raw text but not converted to a DATE,
    # avoiding fabricated month/day precision.
    return None


def _title_company(line: str) -> tuple[str, str] | None:
    if "|" in line:
        title, company = (part.strip() for part in line.split("|", 1))
    else:
        match = re.fullmatch(r"(.+?)\s+(?:at|tại)\s+(.+)", line, re.I)
        if not match:
            return None
        title, company = match.group(1).strip(), match.group(2).strip()
    if not title or not company or len(title) > 150 or len(company) > 150:
        return None
    return title, company


def extract_experiences(section_text: str) -> tuple[ParsedExperience, ...]:
    results: list[ParsedExperience] = []
    for block in _blocks(section_text):
        pair = _title_company(block[0]) if block else None
        if pair is None:
            continue
        title, company = pair
        start_date: date | None = None
        end_date: date | None = None
        is_current = False
        date_range_seen = False
        description_lines: list[str] = []
        for line in block[1:]:
            match = _RANGE.fullmatch(line)
            if match and not date_range_seen:
                date_range_seen = True
                start_date = _parse_month_date(match.group(1))
                end_value = match.group(2).casefold()
                is_current = end_value in _CURRENT_MARKERS
                end_date = None if is_current else _parse_month_date(match.group(2))
                if start_date is not None and end_date is not None and end_date < start_date:
                    start_date = None
                    end_date = None
                continue
            description_lines.append(line)
        results.append(
            ParsedExperience(
                company_name=company,
                job_title=title,
                start_date=start_date,
                end_date=end_date,
                is_current=is_current,
                description="\n".join(description_lines) or None,
            )
        )
    return tuple(results)
