"""DAP (digital adoption) tour models.

A `Tour` is a workspace-scoped, versioned experience: a multi-step flow, a
banner, or an announcement modal (`kind`), authored in the dashboard or via the
Chrome extension and delivered to the embeddable widget. Steps anchor to CSS
selectors (with ranked fallbacks + an opaque `target` descriptor for
self-healing resolution). `TourEvent` rows capture playback telemetry
(started / step_viewed / completed / dismissed / step_error) that powers the
funnel stats. `version` bumps whenever step *content* changes so the widget can
invalidate a tour a visitor is mid-way through (target/screenshot updates do
not bump).

`contact_id` on TourEvent is a bare GUID (not an FK): telemetry is written by the
public widget endpoint from a possibly-stale contact token and must never fail an
insert or be cascaded away — it is only ever read filtered by tour/workspace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime, utcnow
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class Tour(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "tours"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # "flow" | "banner" | "announcement" — banners/announcements are single-step tours.
    kind: Mapped[str] = mapped_column(String(20), default="flow", server_default="flow")
    # "draft" | "live" | "paused"
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # {"type": "manual"} | {"type": "url_match", "url_pattern": "*/inbox*"}
    trigger: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # {"type": "all"} | {"type": "filters", "filters": [<segment-filter>, ...]}
    audience: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # {"start_at": iso?, "end_at": iso?} — UTC delivery window; empty = always.
    schedule: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON, default=dict, server_default="{}"
    )
    # {"type": "once"|"until_completed"|"until_dismissed"|"every_time", "cooldown_hours": int?}
    frequency: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON, default=dict, server_default="{}"
    )
    # Higher first in delivery ordering.
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # {"mode": "guided"|"driven", "backdrop", "show_progress", "dismissable"}
    settings: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON, default=dict, server_default="{}"
    )
    # v2 step dicts — see app.schemas.tours.TourStepIn for the full shape.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    # {"accent": "#6366f1", "position": "top"|"bottom"?} (position used by banner kind)
    theme: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )


class TourEvent(WorkspaceScopedMixin, Base):
    """Playback telemetry for a tour (drives the funnel stats)."""

    __tablename__ = "tour_events"
    __table_args__ = (Index("ix_tour_events_tour_created", "tour_id", "created_at"),)

    id: Mapped[str] = pk()
    tour_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("tours.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Bare GUID (see module docstring) — nullable for anonymous visitors.
    contact_id: Mapped[str | None] = mapped_column(GUID, index=True)
    # "started" | "step_viewed" | "completed" | "dismissed" | "step_error"
    event: Mapped[str] = mapped_column(String(20), nullable=False)
    step_index: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
