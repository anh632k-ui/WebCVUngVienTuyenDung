from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from unicodedata import normalize

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from app.schemas.match_schema import MatchSummary


class ParsingStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PARSED = "PARSED"
    FAILED = "FAILED"


class JobStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


def _decimal_weight_from_json_number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("Job weights must be JSON numbers")
    decimal_value = Decimal(str(value))
    return Decimal("0") if decimal_value == 0 else decimal_value


JobCreateWeight = Annotated[
    Decimal,
    BeforeValidator(_decimal_weight_from_json_number),
    Field(ge=0, le=1, decimal_places=3),
    WithJsonSchema({"type": "number", "minimum": 0, "maximum": 1}),
]


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    title: str = Field(min_length=1, max_length=200)
    job_level: str = Field(min_length=1, max_length=50)
    location: str | None = Field(default=None, max_length=150)
    raw_content: str = Field(min_length=1)
    w_skill: JobCreateWeight = Field(default=Decimal("0.500"), json_schema_extra={"default": 0.5})
    w_semantic: JobCreateWeight = Field(
        default=Decimal("0.300"), json_schema_extra={"default": 0.3}
    )
    w_experience: JobCreateWeight = Field(
        default=Decimal("0.200"), json_schema_extra={"default": 0.2}
    )

    @field_validator("title", "job_level", mode="before")
    @classmethod
    def normalize_trimmed_required_text(cls, value: object) -> object:
        return normalize("NFC", value).strip() if isinstance(value, str) else value

    @field_validator("location", mode="before")
    @classmethod
    def normalize_optional_location(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = normalize("NFC", value).strip()
        return normalized or None

    @field_validator("raw_content", mode="before")
    @classmethod
    def normalize_raw_content(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized_lines = value.replace("\r\n", "\n").replace("\r", "\n")
        return normalize("NFC", normalized_lines)

    @model_validator(mode="after")
    def validate_weight_sum(self) -> JobCreateRequest:
        if self.w_skill + self.w_semantic + self.w_experience != Decimal("1.000"):
            raise ValueError("Job weights must sum exactly to 1.000")
        return self


class JobStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: JobStatus


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


class CandidateSummary(BaseModel):
    resume_id: uuid.UUID
    full_name: str | None = None
    current_title: str | None = None


class LeaderboardItem(BaseModel):
    rank: int = Field(ge=1)
    match: MatchSummary
    candidate: CandidateSummary


class LeaderboardResponse(BaseModel):
    success: Literal[True] = True
    data: list[LeaderboardItem]
    meta: PaginationMeta
