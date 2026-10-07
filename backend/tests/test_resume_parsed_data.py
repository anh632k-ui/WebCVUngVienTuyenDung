from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ai.resume_manual_text import build_resume_aggregate_text
from app.schemas.resume_schema import ResumeParsedDataUpdate


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "candidate_profile": None,
        "skills": [],
        "experiences": [],
        "educations": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "missing",
    ["candidate_profile", "skills", "experiences", "educations"],
)
def test_full_replacement_requires_all_top_level_fields(missing: str) -> None:
    payload = _payload()
    del payload[missing]
    with pytest.raises(ValidationError):
        ResumeParsedDataUpdate.model_validate(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"skills": [{"skill_id": 1}, {"skill_id": 1}]},
        {
            "experiences": [
                {
                    "company_name": "Company",
                    "job_title": "Engineer",
                    "start_date": "2025-01-02",
                    "end_date": "2025-01-01",
                }
            ]
        },
        {
            "experiences": [
                {
                    "company_name": "Company",
                    "job_title": "Engineer",
                    "is_current": True,
                    "end_date": "2025-01-01",
                }
            ]
        },
        {
            "educations": [
                {"institution_name": "University", "start_year": 2025, "graduation_year": 2024}
            ]
        },
        {"skills": [{"skill_id": 1, "years_of_experience": "Infinity"}]},
        {"skills": [{"skill_id": 1, "years_of_experience": 1000.0}]},
        {"educations": [{"institution_name": "University", "gpa": 1000.0}]},
        {"educations": [{"institution_name": "University", "start_year": 32768}]},
        {"candidate_profile": {"full_name": "x" * 151}},
        {"experiences": [{"company_name": "bad\x00text", "job_title": "Engineer"}]},
    ],
)
def test_aggregate_validation_rejects_invalid_or_unrepresentable_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ResumeParsedDataUpdate.model_validate(_payload(**overrides))


def test_numeric_values_are_normalized_before_text_and_persistence() -> None:
    payload = ResumeParsedDataUpdate.model_validate(
        _payload(
            skills=[{"skill_id": 7, "years_of_experience": 1.25}],
            educations=[{"institution_name": "University", "gpa": 3.456}],
        )
    )
    assert payload.skills[0].years_of_experience == Decimal("1.3")
    assert payload.educations[0].gpa == Decimal("3.46")
    document = json.loads(build_resume_aggregate_text(payload, skill_names={7: "Python"}))
    assert document["skills"][0]["years_of_experience"] == "1.3"
    assert document["educations"][0]["gpa"] == "3.46"


@pytest.mark.parametrize("value", [-1, -0.05, -0.04, -0.001])
def test_years_of_experience_rejects_negative_numeric_values_before_rounding(
    value: float,
) -> None:
    with pytest.raises(ValidationError):
        ResumeParsedDataUpdate.model_validate(
            _payload(skills=[{"skill_id": 1, "years_of_experience": value}])
        )


@pytest.mark.parametrize("value", [0, 0.001, 0.04, 0.05, 1.25])
def test_years_of_experience_accepts_nonnegative_numeric_values(value: float) -> None:
    payload = ResumeParsedDataUpdate.model_validate(
        _payload(skills=[{"skill_id": 1, "years_of_experience": value}])
    )
    assert payload.skills[0].years_of_experience is not None


def test_canonical_text_is_deterministic_and_uses_taxonomy_names() -> None:
    payload = ResumeParsedDataUpdate.model_validate(
        _payload(
            candidate_profile={"full_name": "Nguyễn Văn A", "current_title": "Engineer"},
            skills=[{"skill_id": 42, "years_of_experience": 2, "proficiency_level": "Senior"}],
            experiences=[
                {
                    "company_name": "Company",
                    "job_title": "Engineer",
                    "start_date": date(2020, 1, 2),
                    "end_date": None,
                }
            ],
            educations=[{"institution_name": "University", "start_year": None}],
        )
    )
    first = build_resume_aggregate_text(payload, skill_names={42: "Canonical Skill"})
    second = build_resume_aggregate_text(payload, skill_names={42: "Canonical Skill"})
    assert first == second
    assert "Canonical Skill" in first
    assert '"skill_id"' not in first
    assert "2020-01-02" in first


def test_empty_aggregate_has_nonempty_structural_embedding_text() -> None:
    payload = ResumeParsedDataUpdate.model_validate(_payload())
    text = build_resume_aggregate_text(payload, skill_names={})
    assert text.strip()
    assert json.loads(text) == _payload()


def test_numeric_request_schema_remains_canonical_json_number() -> None:
    definitions = ResumeParsedDataUpdate.model_json_schema()["$defs"]
    years = definitions["ResumeSkillInput"]["properties"]["years_of_experience"]
    gpa = definitions["EducationInput"]["properties"]["gpa"]
    assert years["anyOf"] == [{"minimum": 0, "type": "number"}, {"type": "null"}]
    assert gpa["anyOf"] == [{"type": "number"}, {"type": "null"}]
