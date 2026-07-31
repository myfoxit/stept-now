"""In-app notifications for workspace members."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class Notification(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_ws_user_read", "workspace_id", "user_id", "read_at"),)

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # assigned|mention|approval|…
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(String(1000))
    link: Mapped[str | None] = mapped_column(String(500))  # app-relative, e.g. /inbox/{id}
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
