from __future__ import annotations

import re

from app.ai.schemas import ParsedProfile
from app.ai.sections import ResumeSection, ResumeSections, identify_section_heading

MAX_SUMMARY_CHARS = 2_000

_EMAIL = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?84|0)(?:[ .-]?\d){9}(?!\d)")
_LINKEDIN = re.compile(
    r"(?:(?:https?://)?(?:www\.)?)linkedin\.com/in/[A-Z0-9_%.-]+/?",
    re.I,
)
_GITHUB = re.compile(r"(?:(?:https?://)?(?:www\.)?)github\.com/[A-Z0-9_.-]+/?", re.I)
_TITLE_LABEL = re.compile(
    r"^(?:current title|title|job title|chức danh|vị trí)\s*:\s*(.+)$",
    re.I,
)
_LOCATION_LABEL = re.compile(r"^(?:location|địa điểm|địa chỉ)\s*:\s*(.+)$", re.I)
_JOB_TITLE_PHRASES = {
    "engineer",
    "developer",
    "manager",
    "analyst",
    "designer",
    "specialist",
    "consultant",
    "architect",
    "scientist",
    "director",
    "intern",
    "kỹ sư",
    "lập trình viên",
}


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0).strip(" \t,;()[]<>") if match else None


def _labeled_value(pattern: re.Pattern[str], text: str) -> str | None:
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match and match.group(1).strip():
            return match.group(1).strip()[:150]
    return None


def _looks_like_name(line: str) -> bool:
    if identify_section_heading(line) is not None or len(line) > 100:
        return False
    if _EMAIL.search(line) or _PHONE.search(line) or _LINKEDIN.search(line) or _GITHUB.search(line):
        return False
    words = line.split()
    if not 2 <= len(words) <= 6:
        return False
    if any(not any(character.isalpha() for character in word) for word in words):
        return False
    if any(
        any(not (character.isalpha() or character in "'-.") for character in word) for word in words
    ):
        return False
    lowered = line.casefold()
    if any(phrase in lowered for phrase in _JOB_TITLE_PHRASES):
        return False
    return all(word[0].isupper() for word in words if word)


def _extract_full_name(sections: ResumeSections) -> str | None:
    candidates = [line.strip() for line in sections.preamble.splitlines() if line.strip()][:10]
    for candidate in candidates:
        if _looks_like_name(candidate):
            return candidate[:150]
    return None


def extract_profile(text: str, sections: ResumeSections) -> ParsedProfile:
    summary = sections.get(ResumeSection.PROFILE).strip() or None
    if summary is not None:
        summary = summary[:MAX_SUMMARY_CHARS].rstrip()
    phone = _first_match(_PHONE, text)
    if phone is not None:
        phone = re.sub(r"\s+", " ", phone)
    return ParsedProfile(
        full_name=_extract_full_name(sections),
        email=_first_match(_EMAIL, text),
        phone_number=phone,
        current_title=_labeled_value(_TITLE_LABEL, sections.preamble),
        location=_labeled_value(_LOCATION_LABEL, sections.preamble),
        linkedin_url=_first_match(_LINKEDIN, text),
        github_url=_first_match(_GITHUB, text),
        professional_summary=summary,
    )
