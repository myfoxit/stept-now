"""User-facing schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.i18n import SUPPORTED_LOCALES, normalize_locale
from app.schemas.common import ORMModel


class UserOut(ORMModel):
    id: str
    email: EmailStr
    name: str
    avatar_url: str | None = None
    #: Chosen dashboard language; null means "follow the browser".
    locale: str | None = None
    created_at: datetime


class UserUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    avatar_url: str | None = Field(None, max_length=500)
    #: A supported locale code, or "" to clear the choice and follow the browser
    #: again. Validated against the shipped set so a typo cannot strand someone
    #: in a language that has no catalog.
    locale: str | None = Field(None, max_length=12)
    preferences: dict[str, Any] | None = None

    @field_validator("locale")
    @classmethod
    def _known_locale(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        code = normalize_locale(value)
        if code is None:
            raise ValueError(f"Unsupported locale; expected one of {', '.join(SUPPORTED_LOCALES)}")
        return code


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)
