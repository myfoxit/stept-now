"""Canned response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

# No spaces — typed after "/" in the composer, e.g. "refund-policy".
SHORTCUT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"


class CannedResponseCreate(BaseModel):
    shortcut: str = Field(min_length=1, max_length=100, pattern=SHORTCUT_PATTERN)
    content: str = Field(min_length=1, max_length=20_000)


class CannedResponseUpdate(BaseModel):
    shortcut: str | None = Field(None, min_length=1, max_length=100, pattern=SHORTCUT_PATTERN)
    content: str | None = Field(None, min_length=1, max_length=20_000)


class CannedResponseOut(ORMModel):
    id: str
    shortcut: str
    content: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
