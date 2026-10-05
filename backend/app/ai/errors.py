from __future__ import annotations

from enum import StrEnum


class ResumeParseErrorKind(StrEnum):
    UNREADABLE_SOURCE = "UNREADABLE_SOURCE"
    ENCRYPTED_PDF = "ENCRYPTED_PDF"
    NO_MEANINGFUL_TEXT = "NO_MEANINGFUL_TEXT"
    TEXT_LIMIT_EXCEEDED = "TEXT_LIMIT_EXCEEDED"
    UNSUPPORTED_MIME_TYPE = "UNSUPPORTED_MIME_TYPE"


class ResumeParseError(Exception):
    """Controlled parser failure without source paths or low-level exception details."""

    def __init__(self, kind: ResumeParseErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
