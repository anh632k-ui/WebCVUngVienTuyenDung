from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.schemas.auth_schema import UserData


class AdminAssignableRole(StrEnum):
    CANDIDATE = "CANDIDATE"
    HR = "HR"


class AdminUserPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = None
    role: AdminAssignableRole | None = None

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one editable field is required")
        if "is_active" in self.model_fields_set and self.is_active is None:
            raise ValueError("is_active cannot be null")
        if "role" in self.model_fields_set and self.role is None:
            raise ValueError("role cannot be null")
        return self


class PaginationMeta(BaseModel):
    page: int
    limit: int
    total_items: int
    total_pages: int


class PaginatedUsersResponse(BaseModel):
    success: Literal[True] = True
    data: list[UserData]
    meta: PaginationMeta
