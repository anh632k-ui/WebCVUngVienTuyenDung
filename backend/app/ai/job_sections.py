from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class JobSection(StrEnum):
    DESCRIPTION = "DESCRIPTION"
    MANDATORY = "MANDATORY"
    OPTIONAL = "OPTIONAL"
    EDUCATION = "EDUCATION"
    EXPERIENCE = "EXPERIENCE"


@dataclass(frozen=True)
class ContextualJobLine:
    text: str
    section: JobSection | None


_LABELS: dict[JobSection, tuple[str, ...]] = {
    JobSection.DESCRIPTION: (
        "job description",
        "description",
        "about the role",
        "mô tả công việc",
    ),
    JobSection.MANDATORY: (
        "requirements",
        "required skills",
        "qualifications",
        "minimum qualifications",
        "yêu cầu",
        "yêu cầu công việc",
        "kỹ năng bắt buộc",
        "bắt buộc",
    ),
    JobSection.OPTIONAL: (
        "preferred qualifications",
        "preferred skills",
        "nice to have",
        "bonus",
        "advantage",
        "ưu tiên",
        "lợi thế",
        "điểm cộng",
        "không bắt buộc",
    ),
    JobSection.EDUCATION: (
        "education",
        "education requirements",
        "trình độ học vấn",
        "học vấn",
        "bằng cấp",
    ),
    JobSection.EXPERIENCE: (
        "experience",
        "experience requirements",
        "kinh nghiệm",
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
    """Recognize only an exact heading or explicit `heading: content` form."""
    normalized = _normalize_heading(line)
    for label, section in _ORDERED_LABELS:
        if normalized in {label, f"{label}:"}:
            return section, ""
        match = re.match(rf"(?i){re.escape(label)}\s*[:\-]\s*", line.strip())
        if match is not None and match.end() < len(line.strip()):
            return section, line.strip()[match.end() :].strip()
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
