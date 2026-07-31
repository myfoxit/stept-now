"""Search analytics: logged knowledge queries + message feedback (thumbs) on AI replies."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, UTCDateTime, utcnow
from app.models.base import WorkspaceScopedMixin, pk


class SearchQuery(WorkspaceScopedMixin, Base):
    """One logged knowledge search — insert-only, aggregated by the analytics API."""

    __tablename__ = "search_queries"
    __table_args__ = (Index("ix_search_queries_ws_created", "workspace_id", "created_at"),)

    id: Mapped[str] = pk()
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    # "playground" | "widget" | "agent" | "copilot" | "api"
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    results_count: Mapped[int] = mapped_column(Integer, nullable=False)
    top_score: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class MessageFeedback(WorkspaceScopedMixin, Base):
    """Thumbs up/down on a message (contacts rate AI answers; teammates rate too).

    One row per (message, actor) — re-rating updates in place. conversation_id and
    message_id are plain GUIDs on purpose: the conversations domain belongs to
    another wave agent (csat.py pattern, no cross-domain FK).
    """

    __tablename__ = "message_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "actor_type", "actor_id", name="uq_message_feedback_actor"),
    )

    id: Mapped[str] = pk()
    conversation_id: Mapped[str] = mapped_column(GUID, index=True, nullable=False)
    message_id: Mapped[str] = mapped_column(GUID, index=True, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(10), nullable=False)  # "contact" | "user"
    actor_id: Mapped[str | None] = mapped_column(GUID)
    rating: Mapped[str] = mapped_column(String(4), nullable=False)  # "up" | "down"
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
