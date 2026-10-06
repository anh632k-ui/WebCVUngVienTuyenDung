from __future__ import annotations

import re
import unicodedata

from app.ai.errors import ResumeParseError, ResumeParseErrorKind

MAX_EXTRACTED_TEXT_CHARS = 1_000_000

_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
_EXCESSIVE_BLANK_LINES = re.compile(r"\n(?:[ \t]*\n){2,}")


def normalize_parser_text(text: str) -> str:
    """Apply shared deterministic semantic text normalization without domain errors."""
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    normalized = "".join(
        character
        for character in normalized
        if character in {"\n", "\t"} or not unicodedata.category(character).startswith("C")
    )
    normalized = _HORIZONTAL_WHITESPACE.sub(" ", normalized)
    normalized = "\n".join(line.strip() for line in normalized.split("\n"))
    return _EXCESSIVE_BLANK_LINES.sub("\n\n", normalized).strip()


def normalize_extracted_text(text: str) -> str:
    """Normalize parser text deterministically without changing letter case or accents."""

    normalized = normalize_parser_text(text)
    if len(normalized) > MAX_EXTRACTED_TEXT_CHARS:
        raise ResumeParseError(
            ResumeParseErrorKind.TEXT_LIMIT_EXCEEDED,
            "Extracted resume text exceeds the parser limit",
        )
    return normalized


def require_meaningful_text(text: str) -> str:
    normalized = normalize_extracted_text(text)
    if not any(character.isalnum() for character in normalized):
        raise ResumeParseError(
            ResumeParseErrorKind.NO_MEANINGFUL_TEXT,
            "Resume contains no meaningful extractable text",
        )
    return normalized
