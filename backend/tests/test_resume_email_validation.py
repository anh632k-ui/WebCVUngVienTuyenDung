from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.ai.entity_recognizer import extract_profile
from app.ai.sections import split_resume_sections
from app.core.email_validation import normalize_untrusted_profile_email
from app.models.resume import CandidateProfile as StoredCandidateProfile
from app.schemas.resume_schema import CandidateProfile, CandidateProfileData


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("candidate@example.com", "candidate@example.com"),
        ("NAME@EXAMPLE.COM", "NAME@example.com"),
        ("candidate@example.invalid", None),
        ("not-an-email", None),
        (None, None),
        (42, None),
    ],
)
def test_untrusted_profile_email_uses_response_email_validation(
    value: object, expected: str | None
) -> None:
    assert normalize_untrusted_profile_email(value) == expected


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("candidate@example.com", "candidate@example.com"),
        ("candidate@example.invalid", None),
    ],
)
def test_parser_only_persists_emails_accepted_by_response_schema(
    email: str, expected: str | None
) -> None:
    text = f"Jane Candidate\nEmail: {email}\nBackend Engineer"
    result = extract_profile(text, split_resume_sections(text))
    assert result.email == expected
    assert result.full_name == "Jane Candidate"


def test_parser_skips_bad_email_and_keeps_next_valid_email() -> None:
    text = "Jane Candidate\nold@example.invalid\nnew@example.com"
    result = extract_profile(text, split_resume_sections(text))
    assert result.email == "new@example.com"


def test_legacy_persisted_email_is_hidden_only_in_response() -> None:
    stored = StoredCandidateProfile(
        resume_id=uuid.uuid4(),
        full_name="Jane Candidate",
        email="old@example.invalid",
    )
    response = CandidateProfileData.model_validate(stored)
    assert response.full_name == "Jane Candidate"
    assert response.email is None
    assert stored.email == "old@example.invalid"


def test_valid_persisted_email_remains_visible() -> None:
    stored = StoredCandidateProfile(
        resume_id=uuid.uuid4(),
        email="candidate@example.com",
    )
    response = CandidateProfileData.model_validate(stored)
    assert response.email == "candidate@example.com"


def test_manual_profile_input_keeps_strict_email_validation() -> None:
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate({"email": "candidate@example.invalid"})
