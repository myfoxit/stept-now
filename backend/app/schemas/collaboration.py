"""Schemas for conversation participants and @mentions."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ParticipantOut(ORMModel):
    id: str
    user_id: str
    reason: str
    muted: bool
    created_at: datetime


class ParticipantAdd(BaseModel):
    user_id: str


class MentionOut(BaseModel):
    id: str
    conversation_id: str
    conversation_number: int | None = None
    conversation_subject: str | None = None
    message_id: str
    excerpt: str = ""
    author_name: str = ""
    read_at: datetime | None = None
    created_at: datetime


class MentionReadRequest(BaseModel):
    """Null conversation_id marks every unread mention read."""

    conversation_id: str | None = None


class MentionReadResult(BaseModel):
    marked: int = Field(description="How many mentions were flipped to read")
