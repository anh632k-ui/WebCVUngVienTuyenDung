from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SkillKind(StrEnum):
    HARD = "HARD"
    SOFT = "SOFT"


class SkillData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    normalized_name: str
    skill_kind: SkillKind
    category: str


class PaginationMeta(BaseModel):
    page: int
    limit: int
    total_items: int
    total_pages: int


class PaginatedSkillsResponse(BaseModel):
    success: Literal[True] = True
    data: list[SkillData]
    meta: PaginationMeta
