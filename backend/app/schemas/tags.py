"""Tag schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

HEX_COLOR = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field("#6b7280", pattern=HEX_COLOR)


class TagUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    color: str | None = Field(None, pattern=HEX_COLOR)


class TagOut(ORMModel):
    id: str
    name: str
    color: str
    created_at: datetime
