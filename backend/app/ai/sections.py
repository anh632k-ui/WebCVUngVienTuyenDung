from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class ResumeSection(StrEnum):
    PROFILE = "PROFILE"
    SKILLS = "SKILLS"
    EXPERIENCE = "EXPERIENCE"
    EDUCATION = "EDUCATION"


@dataclass(frozen=True)
class ResumeSections:
    preamble: str
    sections: dict[ResumeSection, str]

    def get(self, section: ResumeSection) -> str:
        return self.sections.get(section, "")


_LABELS: dict[ResumeSection, set[str]] = {
    ResumeSection.PROFILE: {
        "summary",
        "professional summary",
        "profile",
        "objective",
        "giới thiệu",
        "tóm tắt",
        "mục tiêu nghề nghiệp",
    },
    ResumeSection.SKILLS: {
        "skills",
        "technical skills",
        "core skills",
        "kỹ năng",
        "kỹ năng chuyên môn",
    },
    ResumeSection.EXPERIENCE: {
        "experience",
        "work experience",
        "employment",
        "professional experience",
        "kinh nghiệm",
        "kinh nghiệm làm việc",
    },
    ResumeSection.EDUCATION: {
        "education",
        "academic background",
        "học vấn",
        "giáo dục",
    },
}


def normalize_heading(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip().casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.rstrip(":").strip()


_HEADING_LOOKUP = {
    normalize_heading(label): section for section, labels in _LABELS.items() for label in labels
}


def identify_section_heading(line: str) -> ResumeSection | None:
    return _HEADING_LOOKUP.get(normalize_heading(line))


def split_resume_sections(text: str) -> ResumeSections:
    preamble: list[str] = []
    collected: dict[ResumeSection, list[str]] = {}
    current: ResumeSection | None = None
    for line in text.splitlines():
        heading = identify_section_heading(line)
        if heading is not None:
            current = heading
            collected.setdefault(heading, [])
            continue
        if current is None:
            preamble.append(line)
        else:
            collected[current].append(line)
    return ResumeSections(
        preamble="\n".join(preamble).strip(),
        sections={section: "\n".join(lines).strip() for section, lines in collected.items()},
    )
