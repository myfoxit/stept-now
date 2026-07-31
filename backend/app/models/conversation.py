"""Conversations: per-workspace numbered threads with Chatwoot-style tracker columns.

Tracker semantics (docs/research/chatwoot.md §3):
- waiting_since   set on creation and on each inbound public message if blank;
                  cleared on human/agent public reply and on resolve.
- first_reply_at  set once, on the first outbound public reply by a user/agent.
- last_activity_at bumped on every message (list sort key).
- agent/contact_last_seen_at power unread counts (timestamps, not counters).

Status: "open" (human-owned) | "pending" (AI-agent-owned) | "snoozed" | "resolved".
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime, utcnow
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class ConversationStatus(enum.StrEnum):
    OPEN = "open"
    PENDING = "pending"  # owned by an AI agent; humans take over by opening
    SNOOZED = "snoozed"
    RESOLVED = "resolved"


class ConversationPriority(enum.StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class ConversationCounter(Base):
    """Per-workspace monotonic counter backing human-friendly conversation numbers."""

    __tablename__ = "conversation_counters"

    workspace_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Conversation(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "number", name="uq_conversations_ws_number"),
        Index("ix_conversations_ws_status_activity", "workspace_id", "status", "last_activity_at"),
        Index("ix_conversations_ws_assignee", "workspace_id", "assignee_user_id"),
        Index("ix_conversations_ws_contact", "workspace_id", "contact_id"),
    )

    id: Mapped[str] = pk()
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # per-workspace sequential
    inbox_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("inboxes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    contact_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("contacts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    contact_inbox_id: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("contact_inboxes.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), default=ConversationStatus.OPEN, nullable=False)
    snoozed_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    priority: Mapped[str] = mapped_column(
        String(10), default=ConversationPriority.NONE, nullable=False
    )
    assignee_user_id: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    # Plain GUID — teams belong to another wave agent, no FK constraint by contract.
    team_id: Mapped[str | None] = mapped_column(GUID)
    subject: Mapped[str | None] = mapped_column(String(400))
    attributes: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # Active AI agent (agent engine's domain) — plain GUID, no FK constraint.
    ai_agent_id: Mapped[str | None] = mapped_column(GUID)

    # Tracker columns (see module docstring).
    waiting_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_reply_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    agent_last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    contact_last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    csat_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ConversationTag(TimestampMixin, WorkspaceScopedMixin, Base):
    """Join between conversations and the workspace Tag table (owned by the
    directory agent — tag_id is a plain GUID, no FK constraint by contract)."""

    __tablename__ = "conversation_tags"
    __table_args__ = (
        UniqueConstraint("conversation_id", "tag_id", name="uq_conversation_tags_pair"),
    )

    id: Mapped[str] = pk()
    conversation_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tag_id: Mapped[str] = mapped_column(GUID, index=True, nullable=False)
