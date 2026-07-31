"""Canned responses (saved replies triggered by a shortcut, e.g. "/refund-policy")."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class CannedResponse(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "canned_responses"
    __table_args__ = (
        UniqueConstraint("workspace_id", "shortcut", name="uq_canned_responses_ws_shortcut"),
    )

    id: Mapped[str] = pk()
    # No spaces; typed after "/" in the composer, e.g. "refund-policy".
    shortcut: Mapped[str] = mapped_column(String(100), nullable=False)
    # Markdown; supports {{contact.name}} / {{agent.name}} placeholders at send time.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
