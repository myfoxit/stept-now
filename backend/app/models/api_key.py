"""Workspace API keys (hashed at rest; the full key is shown exactly once)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class ApiKey(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), nullable=False)  # display: sk_stept_ab12…
    hashed_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
