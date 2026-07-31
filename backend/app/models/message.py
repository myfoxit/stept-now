"""Messages: the conversation timeline (public replies, private notes, activity).

Chatwoot mapping (docs/research/chatwoot.md): direction ≈ message_type
incoming/outgoing, visibility "note" ≈ private bool, "activity" ≈ activity
message_type (free audit timeline). `source_id` is the channel-native message id
(email Message-ID, slack ts, …) and dedupes inbound ingestion via a unique
constraint per conversation (NULLs are distinct on both dialects).
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class MessageDirection(enum.StrEnum):
    IN = "in"
    OUT = "out"


class MessageVisibility(enum.StrEnum):
    PUBLIC = "public"
    NOTE = "note"  # internal note — never delivered / never shown to the contact
    ACTIVITY = "activity"  # system timeline entries ("Sam resolved the conversation")


class AuthorType(enum.StrEnum):
    CONTACT = "contact"
    USER = "user"  # human teammate
    AGENT = "agent"  # AI agent
    SYSTEM = "system"


class DeliveryStatus(enum.StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class Message(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "source_id", name="uq_messages_conversation_source"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = pk()
    conversation_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    direction: Mapped[str] = mapped_column(String(3), nullable=False)  # "in" | "out"
    visibility: Mapped[str] = mapped_column(
        String(10), default=MessageVisibility.PUBLIC, nullable=False
    )
    author_type: Mapped[str] = mapped_column(String(20), nullable=False)
    author_id: Mapped[str | None] = mapped_column(GUID)
    # Denormalized for display — survives author deletion/rename.
    author_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # markdown
    # [{key, name, size, content_type}] — binary lives in app.core.storage.
    attachments: Mapped[list[Any]] = mapped_column(PortableJSON, default=list, nullable=False)
    # Channel-native message id (dedupe key); unique per conversation when set.
    source_id: Mapped[str | None] = mapped_column(String(300))
    # "pending" | "sent" | "failed"; NULL for inbound and notes/activity.
    delivery_status: Mapped[str | None] = mapped_column(String(10))
    delivery_error: Mapped[str | None] = mapped_column(Text)
    # Citations [{n,title,url,document_id}], agent_run_id, email headers, …
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
