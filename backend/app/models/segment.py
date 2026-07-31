"""Contact segments: saved filter lists over the contact directory.

Filter schema (AND semantics), stored as a JSON list:
    [{"field": "email|name|external_id|last_seen_at|created_at|verified|attributes.<key>",
      "op": "eq|neq|contains|starts_with|exists|not_exists|gt|lt", "value": ...}]
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class Segment(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "segments"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    filters: Mapped[list[dict[str, Any]]] = mapped_column(
        PortableJSON, default=list, nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
