from __future__ import annotations

import re
import unicodedata

from app.ai.job_errors import JobParseError, JobParseErrorKind

MAX_JOB_TEXT_CHARS = 1_000_000

_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
_EXCESSIVE_BLANK_LINES = re.compile(r"\n(?:[ \t]*\n){2,}")


def normalize_job_text(text: str) -> str:
    """Normalize bounded JD text without changing casing, accents, or meaning."""
    if not isinstance(text, str):
        raise JobParseError(JobParseErrorKind.INVALID_TEXT, "Job text must be a string")
    if len(text) > MAX_JOB_TEXT_CHARS:
        raise JobParseError(
            JobParseErrorKind.TEXT_LIMIT_EXCEEDED,
            "Job text exceeds the parser limit",
        )
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise JobParseError(
            JobParseErrorKind.INVALID_TEXT, "Job text is not valid Unicode"
        ) from error

    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    normalized = "".join(
        character
        for character in normalized
        if character in {"\n", "\t"} or not unicodedata.category(character).startswith("C")
    )
    normalized = _HORIZONTAL_WHITESPACE.sub(" ", normalized)
    normalized = "\n".join(line.strip() for line in normalized.split("\n"))
    normalized = _EXCESSIVE_BLANK_LINES.sub("\n\n", normalized).strip()
    if not any(character.isalnum() for character in normalized):
        raise JobParseError(
            JobParseErrorKind.NO_MEANINGFUL_TEXT,
            "Job text contains no meaningful alphanumeric content",
        )
    return normalized
