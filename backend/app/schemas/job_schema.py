from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.match_schema import MatchSummary, PaginationMeta


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
