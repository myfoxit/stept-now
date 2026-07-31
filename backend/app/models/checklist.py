"""Onboarding checklist models (DAP).

A `Checklist` is a workspace-scoped, versioned list of onboarding items rendered
by the widget as a launcher pill plus a panel. Items carry an optional *action*
(start a tour / open a url / open the messenger) and a *completion* rule (manual
checkbox, "that tour was completed", or "that url was visited").

`ChecklistProgress` stores per-contact completion state — exactly one row per
(checklist, contact). Anonymous visitors keep their progress client-side, which
is why `contact_id` is NOT NULL here. Like `TourEvent.contact_id` it is a bare
GUID (not an FK): progress is written by the public widget endpoint from a
possibly-stale contact token and must never fail an insert or be cascaded away —
it is only ever read filtered by checklist/workspace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class Checklist(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "checklists"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # "draft" | "live" | "paused"
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # [{"id", "title", "body" (markdown), "action": {...}, "completion": {...}}] — max 20
    items: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    # {"type": "manual"} | {"type": "url_match", "url_pattern": "*/inbox*"} (tour shape)
    trigger: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # {"type": "all"} | {"type": "filters", "filters": [<segment-filter>, ...]}
    audience: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # {"accent": "#6366f1", "position": "bottom-right" | "bottom-left"}
    theme: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # {"label": "Getting started", "auto_open_once": true}
    launcher: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )


class ChecklistProgress(TimestampMixin, WorkspaceScopedMixin, Base):
    """One row per (checklist, identified contact)."""

    __tablename__ = "checklist_progress"
    __table_args__ = (
        UniqueConstraint(
            "checklist_id", "contact_id", name="uq_checklist_progress_checklist_contact"
        ),
    )

    id: Mapped[str] = pk()
    checklist_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("checklists.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Bare GUID (see module docstring); never anonymous.
    contact_id: Mapped[str] = mapped_column(GUID, index=True, nullable=False)
    # {item_id: iso8601 completed_at}
    item_state: Mapped[dict[str, str]] = mapped_column(PortableJSON, default=dict, nullable=False)
    dismissed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
