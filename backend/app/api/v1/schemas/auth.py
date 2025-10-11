"""Authentication API schemas."""

from __future__ import annotations

from uuid import UUID

from pydantic import EmailStr, Field

from app.api.v1.schemas.registration import CamelModel


class PasswordLoginRequest(CamelModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class PasswordLoginResponse(CamelModel):
    access_token: str
    token: str
    token_type: str
    expires_in: int
    user_id: UUID
    email: EmailStr
    first_name: str
