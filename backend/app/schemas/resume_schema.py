from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ParsingStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PARSED = "PARSED"
    FAILED = "FAILED"


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
