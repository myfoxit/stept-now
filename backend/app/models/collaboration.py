"""Thread collaboration: participants (watchers) and @mentions.

A participant watches a conversation and receives its notifications; membership
is implicit (assignee, note authors) or explicit (someone @mentions you, or you
subscribe). A mention row is the audit trail of one `@name` inside one note —
it is what powers the "Mentions" inbox view.

See docs/CHATWOOT-BACKLOG.md §1.2.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, UTCDateTime, utcnow
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class ConversationParticipant(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "conversation_participants"
    __table_args__ = (UniqueConstraint("conversation_id", "user_id", name="uq_conv_participant"),)

    id: Mapped[str] = pk()
    conversation_id: Mapped[str] = mapped_column(GUID, index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # How they joined: "assignee" | "note" | "mention" | "manual". Informational —
    # every participant is notified the same way.
    reason: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    # Explicit opt-out: kept as a row so implicit re-adds don't resurrect a mute.
    muted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Mention(WorkspaceScopedMixin, Base):
    __tablename__ = "mentions"

    id: Mapped[str] = pk()
    conversation_id: Mapped[str] = mapped_column(GUID, index=True, nullable=False)
    message_id: Mapped[str] = mapped_column(GUID, index=True, nullable=False)
    # Who was mentioned.
    user_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Who wrote the note (null for system/AI authors).
    author_id: Mapped[str | None] = mapped_column(GUID, ForeignKey("users.id", ondelete="SET NULL"))
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
