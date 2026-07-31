"""Shared model mixins."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.db import GUID, UTCDateTime, utcnow, uuid7


def pk() -> Mapped[str]:
    return mapped_column(GUID, primary_key=True, default=uuid7)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class WorkspaceScopedMixin:
    """Adds an indexed workspace FK — every tenant-owned table uses this."""

    @declared_attr
    def workspace_id(cls) -> Mapped[str]:
        return mapped_column(
            GUID, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
        )
