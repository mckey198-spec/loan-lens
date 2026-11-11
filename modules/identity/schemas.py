"""Identity API and service schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

UserRole = Literal["loan_officer", "reviewer", "admin"]
APPROVED_ROLES: frozenset[str] = frozenset({"loan_officer", "reviewer", "admin"})


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool


class CreateUserRequest(BaseModel):
    email: str = Field(min_length=1, max_length=255)
    full_name: str = Field(min_length=1, max_length=512)
    role: UserRole
    temporary_password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("full_name")
    @classmethod
    def strip_full_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("full_name must not be blank")
        return stripped


class UpdateUserRequest(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None


class UserListResponse(BaseModel):
    items: list[UserProfile]
    offset: int
    limit: int
    total: int


class LoginResponse(BaseModel):
    csrf_token: str
    user: UserProfile


class AuthenticatedPrincipal(BaseModel):
    """Resolved session + user after cookie (and CSRF for mutations) checks."""

    user: UserProfile
    session_id: UUID
    csrf_token: str
    raw_session_secret: str
    session_expires_at: datetime
    session_last_seen_at: datetime
