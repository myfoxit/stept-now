"""Conversation schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

StatusLiteral = Literal["open", "pending", "snoozed", "resolved"]
PriorityLiteral = Literal["none", "low", "medium", "high", "urgent"]


class ContactRef(BaseModel):
    id: str
    name: str
    email: str | None = None
    avatar_url: str | None = None


class ContactDetail(ContactRef):
    external_id: str | None = None
    phone: str | None = None
    verified: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)
    last_seen_at: datetime | None = None
    created_at: datetime | None = None


class InboxRef(BaseModel):
    id: str
    name: str
    channel_type: str


class AssigneeRef(BaseModel):
    id: str
    name: str


class ConversationListItem(BaseModel):
    id: str
    number: int
    subject: str | None = None
    status: str
    priority: str
    contact: ContactRef
    inbox: InboxRef
    assignee: AssigneeRef | None = None
    # First 140 chars of the latest public message (notes/activity excluded).
    last_message_preview: str | None = None
    last_activity_at: datetime
    unread: bool = False
    tag_ids: list[str] = Field(default_factory=list)
    waiting_since: datetime | None = None


class ConversationOut(BaseModel):
    """Full detail — also the payload of conversation.* realtime broadcasts."""

    id: str
    number: int
    subject: str | None = None
    status: str
    priority: str
    snoozed_until: datetime | None = None
    contact: ContactDetail
    inbox: InboxRef
    assignee: AssigneeRef | None = None
    team_id: str | None = None
    ai_agent_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    waiting_since: datetime | None = None
    first_reply_at: datetime | None = None
    resolved_at: datetime | None = None
    last_activity_at: datetime
    agent_last_seen_at: datetime | None = None
    contact_last_seen_at: datetime | None = None
    csat_requested: bool = False
    tag_ids: list[str] = Field(default_factory=list)
    unread_count: int = 0
    created_at: datetime


class ConversationCreate(BaseModel):
    """Outbound start: an agent opens a conversation with a contact."""

    contact_id: str
    inbox_id: str
    content: str = Field(min_length=1, max_length=150_000)
    subject: str | None = Field(None, max_length=400)


class ConversationPatch(BaseModel):
    """Explicit null matters for assignee_user_id/team_id (null = unassign);
    routers check model_fields_set to distinguish omitted from null."""

    status: StatusLiteral | None = None
    snoozed_until: datetime | None = None
    priority: PriorityLiteral | None = None
    assignee_user_id: str | None = None
    team_id: str | None = None


class TagRequest(BaseModel):
    tag_id: str


class ConversationCounts(BaseModel):
    """Sidebar badge numbers."""

    open: int = 0
    unassigned: int = 0
    mine: int = 0
    pending: int = 0
    snoozed: int = 0
    resolved: int = 0
