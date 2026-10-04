from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MatchStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MatchSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    resume_id: uuid.UUID
    generation: int = Field(ge=1)
    resume_revision: int = Field(ge=1)
    job_revision: int = Field(ge=1)
    status: MatchStatus
    overall_score: float | None = Field(default=None, ge=0, le=100)
    skill_score: float | None = Field(default=None, ge=0, le=100)
    semantic_score: float | None = Field(default=None, ge=0, le=100)
    experience_score: float | None = Field(default=None, ge=0, le=100)
    algorithm_version: str
    embedding_model: str | None = None
    embedding_preprocessing_version: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    calculated_at: datetime | None = None


class MatchDetail(MatchSummary):
    matched_skills: list[dict[str, Any]] = Field(default_factory=list)
    missing_skills: list[dict[str, Any]] = Field(default_factory=list)
    gap_analysis_summary: str | None = None


class PaginationMeta(BaseModel):
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class PaginatedMatchesResponse(BaseModel):
    success: Literal[True] = True
    data: list[MatchSummary]
    meta: PaginationMeta


class MatchResponse(BaseModel):
    success: Literal[True] = True
    data: MatchDetail
