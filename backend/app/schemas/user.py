"""User-facing schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel


class UserOut(ORMModel):
    id: str
    email: EmailStr
    name: str
    avatar_url: str | None = None
    created_at: datetime


class UserUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    avatar_url: str | None = Field(None, max_length=500)
    preferences: dict[str, Any] | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)
