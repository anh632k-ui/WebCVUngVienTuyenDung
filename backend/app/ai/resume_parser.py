from __future__ import annotations

from collections.abc import Sequence

from app.ai.education_extractor import extract_educations
from app.ai.entity_recognizer import extract_profile
from app.ai.experience_extractor import extract_experiences
from app.ai.pdf_docx_extractor import extract_resume_text
from app.ai.schemas import ParsedResumeResult, TaxonomySkill
from app.ai.sections import ResumeSection, split_resume_sections
from app.ai.skill_normalizer import normalize_skills

RESUME_TEXT_PREPROCESSING_VERSION = "resume-text-v1"


def parse_resume(
    source_bytes: bytes,
    mime_type: str,
    taxonomy: Sequence[TaxonomySkill],
) -> ParsedResumeResult:
    """Parse an uploaded resume into a deterministic in-memory result only."""

    raw_text = extract_resume_text(source_bytes, mime_type)
    sections = split_resume_sections(raw_text)
    return ParsedResumeResult(
        raw_text=raw_text,
        profile=extract_profile(raw_text, sections),
        skills=normalize_skills(raw_text, taxonomy),
        experiences=extract_experiences(sections.get(ResumeSection.EXPERIENCE)),
        educations=extract_educations(sections.get(ResumeSection.EDUCATION)),
    )
