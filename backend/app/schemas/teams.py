"""Team schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.user import UserOut


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    icon: str | None = Field(None, max_length=20)
    description: str | None = Field(None, max_length=400)


class TeamUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    icon: str | None = Field(None, max_length=20)
    description: str | None = Field(None, max_length=400)


class TeamMemberAdd(BaseModel):
    user_id: str


class TeamOut(BaseModel):
    id: str
    name: str
    icon: str | None = None
    description: str | None = None
    members: list[UserOut] = Field(default_factory=list)
    created_at: datetime
