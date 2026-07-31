"""Inbox schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

ChannelTypeLiteral = Literal[
    "widget",
    "email",
    "slack",
    "telegram",
    "api",
    "whatsapp",
    "messenger",
    "instagram",
    "sms",
    "line",
]


class InboxCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    channel_type: ChannelTypeLiteral
    config: dict[str, Any] = Field(default_factory=dict)
    # Channel credentials — encrypted at rest, never returned by the API.
    secrets: dict[str, Any] | None = None
    enabled: bool = True


class InboxUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    # Providing secrets re-encrypts them; an empty dict clears stored secrets.
    secrets: dict[str, Any] | None = None


class InboxOut(ORMModel):
    id: str
    name: str
    channel_type: str
    enabled: bool
    config: dict[str, Any]
    widget_key: str | None = None
    has_secrets: bool = False
    # Widget inboxes only: the copy-paste embed <script> snippet.
    embed_snippet: str | None = None
    created_at: datetime
    updated_at: datetime
