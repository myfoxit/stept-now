"""DAP (digital adoption) tour models.

A `Tour` is a workspace-scoped, versioned sequence of steps — each anchored to a
CSS selector on the customer's own product — authored in the dashboard (or via
the Chrome tour-recorder extension) and delivered to the embeddable widget.
`TourEvent` rows capture playback telemetry (started / step_viewed / completed /
dismissed) that powers the funnel stats. `version` bumps whenever steps change so
the widget can invalidate a tour a visitor is mid-way through.

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
    # "draft" | "live" | "paused"
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # {"type": "manual"} | {"type": "url_match", "url_pattern": "*/inbox*"}
    trigger: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # {"type": "all"} | {"type": "filters", "filters": [<segment-filter>, ...]}
    audience: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # [{"id", "selector", "title", "body", "placement"}]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    # {"accent": "#6366f1"}
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
    # "started" | "step_viewed" | "completed" | "dismissed"
    event: Mapped[str] = mapped_column(String(20), nullable=False)
    step_index: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
