"""Tags + contact/tag association.

The Tag table is shared with conversation tagging (agent B's `ConversationTag`
association lives in the conversation domain but FKs `tags.id`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, UTCDateTime, utcnow
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class Tag(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_tags_ws_name"),)

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Hex color for UI chips, e.g. "#ef4444".
    color: Mapped[str] = mapped_column(String(20), default="#6b7280", nullable=False)


class ContactTag(WorkspaceScopedMixin, Base):
    __tablename__ = "contact_tags"
    __table_args__ = (UniqueConstraint("contact_id", "tag_id", name="uq_contact_tags_pair"),)

    id: Mapped[str] = pk()
    contact_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("contacts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tag_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("tags.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
