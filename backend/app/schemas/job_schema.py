from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ParsingStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PARSED = "PARSED"
    FAILED = "FAILED"


class JobStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class JobData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recruiter_id: uuid.UUID
    revision: int = Field(ge=1)
    title: str
    job_level: str
    location: str | None = None
    raw_content: str = Field(default_factory=str)
    min_experience_years: float = Field(default_factory=float, ge=0)
    education_requirement: str | None = None
    parsing_status: ParsingStatus
    is_criteria_verified: bool
    w_skill: float
    w_semantic: float
    w_experience: float
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    parsed_at: datetime | None = None


class PaginationMeta(BaseModel):
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class JobResponse(BaseModel):
    success: Literal[True] = True
    data: JobData


class PaginatedJobsResponse(BaseModel):
    success: Literal[True] = True
    data: list[JobData]
    meta: PaginationMeta
