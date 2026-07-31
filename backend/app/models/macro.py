"""Macros: saved multi-action shortcuts agents run manually on a conversation.

Same ordered-action vocabulary as automation rules ([{"type", "params"}]), but
executed on demand by a member, attributed to that member. `visibility` follows
Chatwoot (docs/research/chatwoot-gaps.md §4): "personal" macros are visible to
their creator only, "global" macros to the whole workspace.
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class MacroVisibility(enum.StrEnum):
    PERSONAL = "personal"
    GLOBAL = "global"


class Macro(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "macros"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Ordered [{"type": ..., "params": {...}}] — automation action vocabulary
    # plus remove_tag; validated against the service allow-list.
    actions: Mapped[list[Any]] = mapped_column(PortableJSON, default=list, nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(10), default=MacroVisibility.PERSONAL, nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
