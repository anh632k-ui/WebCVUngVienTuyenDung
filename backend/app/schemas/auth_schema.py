from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(min_length=1, max_length=100)]
PhoneNumber = Annotated[str, StringConstraints(max_length=20)]
Password = Annotated[str, StringConstraints(min_length=8)]


class UserRole(StrEnum):
    CANDIDATE = "CANDIDATE"
    HR = "HR"
    ADMIN = "ADMIN"


class RegistrationRole(StrEnum):
    CANDIDATE = "CANDIDATE"
    HR = "HR"


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(RequestModel):
    email: EmailStr
    password: Password
    full_name: Name
    phone_number: PhoneNumber | None = None
    role: RegistrationRole


class LoginRequest(RequestModel):
    email: EmailStr
    password: str


class ChangePasswordRequest(RequestModel):
    current_password: str
    new_password: Password


class UserUpdateRequest(RequestModel):
    full_name: Name | None = None
    phone_number: PhoneNumber | None = None

    @model_validator(mode="after")
    def require_at_least_one_editable_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one editable field is required")
        if "full_name" in self.model_fields_set and self.full_name is None:
            raise ValueError("full_name cannot be null")
        return self


class UserData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    phone_number: str | None
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserResponse(BaseModel):
    success: Literal[True] = True
    data: UserData


class LoginData(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = Field(gt=0)
    user: UserData


class LoginResponse(BaseModel):
    success: Literal[True] = True
    data: LoginData


class MessageResponse(BaseModel):
    success: Literal[True] = True
    message: str | None = None
