from __future__ import annotations

from enum import StrEnum


class JobParseErrorKind(StrEnum):
    NO_MEANINGFUL_TEXT = "NO_MEANINGFUL_TEXT"
    TEXT_LIMIT_EXCEEDED = "TEXT_LIMIT_EXCEEDED"
    INVALID_TEXT = "INVALID_TEXT"


class JobParseError(Exception):
    """Controlled pure-parser failure without low-level implementation details."""

    def __init__(self, kind: JobParseErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
