"""Append-only audit log."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, PortableJSON, UTCDateTime, utcnow
from app.models.base import WorkspaceScopedMixin, pk


class AuditLog(WorkspaceScopedMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_ws_created", "workspace_id", "created_at"),)

    id: Mapped[str] = pk()
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)  # user|api_key|agent|system
    actor_id: Mapped[str | None] = mapped_column(String(36))
    actor_label: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(
        String(100), index=True, nullable=False
    )  # e.g. member.update
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(36))
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
