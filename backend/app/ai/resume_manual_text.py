from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal

from app.ai.text_processing import normalize_parser_text
from app.schemas.resume_schema import ResumeParsedDataUpdate


def _decimal_text(value: Decimal | None, places: int) -> str | None:
    return f"{value:.{places}f}" if value is not None else None


def build_resume_aggregate_text(
    payload: ResumeParsedDataUpdate,
    *,
    skill_names: Mapping[int, str],
) -> str:
    """Serialize accepted manual CV data into deterministic baseline embedding text."""

    profile = payload.candidate_profile
    document = {
        "candidate_profile": (
            {
                "full_name": profile.full_name,
                "email": str(profile.email) if profile.email is not None else None,
                "phone_number": profile.phone_number,
                "current_title": profile.current_title,
                "location": profile.location,
                "linkedin_url": profile.linkedin_url,
                "github_url": profile.github_url,
                "professional_summary": profile.professional_summary,
            }
            if profile is not None
            else None
        ),
        "skills": [
            {
                "name": skill_names[item.skill_id],
                "years_of_experience": _decimal_text(item.years_of_experience, 1),
                "proficiency_level": item.proficiency_level,
            }
            for item in payload.skills
        ],
        "experiences": [
            {
                "company_name": item.company_name,
                "job_title": item.job_title,
                "start_date": item.start_date.isoformat() if item.start_date is not None else None,
                "end_date": item.end_date.isoformat() if item.end_date is not None else None,
                "is_current": item.is_current,
                "description": item.description,
            }
            for item in payload.experiences
        ],
        "educations": [
            {
                "institution_name": item.institution_name,
                "degree": item.degree,
                "field_of_study": item.field_of_study,
                "start_year": item.start_year,
                "graduation_year": item.graduation_year,
                "gpa": _decimal_text(item.gpa, 2),
                "description": item.description,
            }
            for item in payload.educations
        ],
    }
    serialized = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    return normalize_parser_text(serialized)
