from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
    model_validator,
)


class ParsingStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PARSED = "PARSED"
    FAILED = "FAILED"


def _normalized_decimal(
    value: object,
    *,
    quantum: Decimal,
    minimum: Decimal,
    maximum: Decimal,
    label: str,
    semantic_minimum: Decimal | None = None,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{label} must be a JSON number")
    decimal_value = Decimal(str(value))
    if not decimal_value.is_finite():
        raise ValueError(f"{label} must be finite")
    if semantic_minimum is not None and decimal_value < semantic_minimum:
        raise ValueError(f"{label} must be at least {semantic_minimum}")
    try:
        normalized = decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise ValueError(f"{label} is not representable") from error
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{label} is not representable")
    return normalized


def _resume_years_from_json_number(value: object) -> Decimal:
    return _normalized_decimal(
        value,
        quantum=Decimal("0.1"),
        minimum=Decimal("0.0"),
        maximum=Decimal("999.9"),
        label="years_of_experience",
        semantic_minimum=Decimal("0"),
    )


def _gpa_from_json_number(value: object) -> Decimal:
    return _normalized_decimal(
        value,
        quantum=Decimal("0.01"),
        minimum=Decimal("-999.99"),
        maximum=Decimal("999.99"),
        label="gpa",
    )


ResumeYears = Annotated[
    Decimal,
    BeforeValidator(_resume_years_from_json_number),
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "minimum": 0}),
]
ResumeGpa = Annotated[
    Decimal,
    BeforeValidator(_gpa_from_json_number),
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
    WithJsonSchema({"type": "number"}),
]


def _validate_database_text(value: str | None) -> str | None:
    if value is None:
        return None
    if "\x00" in value:
        raise ValueError("text values must not contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("text values must be valid UTF-8") from error
    return value


class ResumeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    revision: int = Field(ge=1)
    file_name: str
    file_size: int
    mime_type: str
    parsing_status: ParsingStatus
    is_manually_edited: bool
    created_at: datetime
    parsed_at: datetime | None = None


class ResumeUploadData(BaseModel):
    resume_id: uuid.UUID
    revision: int = Field(ge=1)
    file_name: str
    file_size: int = Field(gt=0)
    parsing_status: ParsingStatus


class ResumeUploadResponse(BaseModel):
    success: Literal[True] = True
    data: ResumeUploadData


class CandidateProfileData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    full_name: str | None = None
    email: EmailStr | None = None
    phone_number: str | None = None
    current_title: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    professional_summary: str | None = None


class ResumeSkillData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    skill_id: int
    years_of_experience: float | None = Field(default=None, ge=0)
    proficiency_level: str | None = None


class ExperienceData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_name: str
    job_title: str
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    description: str | None = None


class EducationData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    institution_name: str
    degree: str | None = None
    field_of_study: str | None = None
    start_year: int | None = None
    graduation_year: int | None = None
    gpa: float | None = None
    description: str | None = None


class CandidateProfile(BaseModel):
    full_name: str | None = Field(default=None, max_length=150)
    email: EmailStr | None = Field(default=None, max_length=255)
    phone_number: str | None = Field(default=None, max_length=30)
    current_title: str | None = Field(default=None, max_length=150)
    location: str | None = Field(default=None, max_length=150)
    linkedin_url: str | None = Field(default=None, max_length=500)
    github_url: str | None = Field(default=None, max_length=500)
    professional_summary: str | None = None

    @field_validator(
        "full_name",
        "phone_number",
        "current_title",
        "location",
        "linkedin_url",
        "github_url",
        "professional_summary",
        mode="after",
    )
    @classmethod
    def validate_database_text(cls, value: str | None) -> str | None:
        return _validate_database_text(value)


class ResumeSkillInput(BaseModel):
    skill_id: int
    years_of_experience: ResumeYears | None = None
    proficiency_level: str | None = Field(default=None, max_length=30)

    @field_validator("proficiency_level", mode="after")
    @classmethod
    def validate_database_text(cls, value: str | None) -> str | None:
        return _validate_database_text(value)


class ExperienceInput(BaseModel):
    company_name: str = Field(max_length=150)
    job_title: str = Field(max_length=150)
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    description: str | None = None

    @field_validator("company_name", "job_title", "description", mode="after")
    @classmethod
    def validate_database_text(cls, value: str | None) -> str | None:
        return _validate_database_text(value)

    @model_validator(mode="after")
    def validate_dates(self) -> ExperienceInput:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must be on or after start_date")
        if self.is_current and self.end_date is not None:
            raise ValueError("end_date must be null when is_current is true")
        return self


class EducationInput(BaseModel):
    institution_name: str = Field(max_length=150)
    degree: str | None = Field(default=None, max_length=100)
    field_of_study: str | None = Field(default=None, max_length=150)
    start_year: int | None = Field(default=None, ge=-32768, le=32767)
    graduation_year: int | None = Field(default=None, ge=-32768, le=32767)
    gpa: ResumeGpa | None = None
    description: str | None = None

    @field_validator("institution_name", "degree", "field_of_study", "description", mode="after")
    @classmethod
    def validate_database_text(cls, value: str | None) -> str | None:
        return _validate_database_text(value)

    @model_validator(mode="after")
    def validate_and_normalize(self) -> EducationInput:
        if (
            self.start_year is not None
            and self.graduation_year is not None
            and self.graduation_year < self.start_year
        ):
            raise ValueError("graduation_year must be on or after start_year")
        return self


class ResumeParsedDataUpdate(BaseModel):
    candidate_profile: CandidateProfile | None
    skills: list[ResumeSkillInput]
    experiences: list[ExperienceInput]
    educations: list[EducationInput]

    @model_validator(mode="after")
    def validate_unique_skills(self) -> ResumeParsedDataUpdate:
        skill_ids = [item.skill_id for item in self.skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("skills must not contain duplicate skill_id values")
        return self


class PaginationMeta(BaseModel):
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class PaginatedResumesResponse(BaseModel):
    success: Literal[True] = True
    data: list[ResumeSummary]
    meta: PaginationMeta


class ResumeDetailData(BaseModel):
    resume: ResumeSummary
    candidate_profile: CandidateProfileData | None
    skills: list[ResumeSkillData]
    experiences: list[ExperienceData]
    educations: list[EducationData]


class ResumeDetailResponse(BaseModel):
    success: Literal[True] = True
    data: ResumeDetailData


class ResumeStatusData(BaseModel):
    resume_id: uuid.UUID
    revision: int = Field(ge=1)
    parsing_status: ParsingStatus
    parsed_at: datetime | None = None
    error_message: str | None = None


class ResumeStatusResponse(BaseModel):
    success: Literal[True] = True
    data: ResumeStatusData
