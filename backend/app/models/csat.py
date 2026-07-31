"""CSAT survey responses (one per conversation, submitted by the contact)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class CsatResponse(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "csat_responses"
    __table_args__ = (
        # Idempotency: one response per conversation (re-submits update in place).
        UniqueConstraint("workspace_id", "conversation_id", name="uq_csat_ws_conversation"),
    )

    id: Mapped[str] = pk()
    # Plain GUID on purpose — the conversations table belongs to another domain.
    conversation_id: Mapped[str] = mapped_column(GUID, index=True, nullable=False)
    contact_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("contacts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..5
    feedback: Mapped[str | None] = mapped_column(Text)
