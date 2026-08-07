"""Saved conversation/contact filters ("views").

A view is a named `ConversationFilter` (see `app.services.filters`) plus a scope:
personal views belong to their creator, shared views are visible workspace-wide.
Chatwoot calls these `custom_filters`; the same query shape also backs report
drill-down and campaign audiences (docs/CHATWOOT-BACKLOG.md §1.3).
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class ViewKind(enum.StrEnum):
    CONVERSATION = "conversation"
    CONTACT = "contact"


class ViewVisibility(enum.StrEnum):
    PERSONAL = "personal"
    SHARED = "shared"


class SavedView(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "saved_views"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default=ViewKind.CONVERSATION, nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(20), default=ViewVisibility.PERSONAL, nullable=False
    )
    # The filter document: {"match": "all"|"any", "conditions": [{field, op, value}, …]}
    query: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    icon: Mapped[str | None] = mapped_column(String(20))  # emoji
    ord: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
