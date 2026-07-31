"""Message schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class AttachmentRef(BaseModel):
    """Reference to an uploaded file (binary lives in app.core.storage)."""

    key: str
    name: str
    size: int = 0
    content_type: str = "application/octet-stream"


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=150_000)
    visibility: Literal["public", "note"] = "public"
    attachments: list[AttachmentRef] = Field(default_factory=list)


class MessageOut(ORMModel):
    id: str
    conversation_id: str
    direction: str
    visibility: str
    author_type: str
    author_id: str | None = None
    author_name: str
    content: str
    attachments: list[dict[str, Any]]
    source_id: str | None = None
    delivery_status: str | None = None
    delivery_error: str | None = None
    meta: dict[str, Any]
    created_at: datetime
