from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class JobSection(StrEnum):
    GENERAL = "GENERAL"
    MANDATORY = "MANDATORY"
    OPTIONAL = "OPTIONAL"
    EDUCATION = "EDUCATION"
    EXPERIENCE = "EXPERIENCE"


@dataclass(frozen=True)
class ContextualJobLine:
    text: str
    section: JobSection | None


_LABELS: dict[JobSection, tuple[str, ...]] = {
    JobSection.GENERAL: (
        "requirements",
        "qualifications",
        "candidate requirements",
        "yêu cầu",
        "yêu cầu ứng viên",
        "điều kiện",
    ),
    JobSection.MANDATORY: (
        "required",
        "required skills",
        "must have",
        "mandatory",
        "yêu cầu bắt buộc",
        "bắt buộc",
    ),
    JobSection.OPTIONAL: (
        "preferred",
        "preferred skills",
        "nice to have",
        "bonus",
        "optional",
        "ưu tiên",
        "lợi thế",
        "điểm cộng",
    ),
    JobSection.EDUCATION: (
        "education",
        "education requirements",
        "academic requirements",
        "học vấn",
        "trình độ học vấn",
        "bằng cấp",
    ),
    JobSection.EXPERIENCE: (
        "experience",
        "experience requirements",
        "work experience",
        "kinh nghiệm",
        "yêu cầu kinh nghiệm",
    ),
}


def _normalize_heading(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value).strip().casefold())


_ORDERED_LABELS = sorted(
    (
        (_normalize_heading(label), section)
        for section, labels in _LABELS.items()
        for label in labels
    ),
    key=lambda item: len(item[0]),
    reverse=True,
)


def split_job_heading(line: str) -> tuple[JobSection, str] | None:
    """Recognize only exact headings or explicit `heading: content` forms."""
    normalized = _normalize_heading(line)
    for label, section in _ORDERED_LABELS:
        if normalized == label or normalized == f"{label}:":
            return section, ""
        match = re.fullmatch(rf"{re.escape(label)}\s*[:\-]\s*(.+)", normalized)
        if match:
            delimiter = re.match(rf"(?i){re.escape(label)}\s*[:\-]\s*", line.strip())
            if delimiter is not None:
                return section, line.strip()[delimiter.end() :].strip()
    return None


def contextual_job_lines(text: str) -> tuple[ContextualJobLine, ...]:
    current: JobSection | None = None
    results: list[ContextualJobLine] = []
    for line in text.splitlines():
        if not line:
            continue
        heading = split_job_heading(line)
        if heading is not None:
            current, remainder = heading
            if remainder:
                results.append(ContextualJobLine(remainder, current))
            continue
        results.append(ContextualJobLine(line, current))
    return tuple(results)
