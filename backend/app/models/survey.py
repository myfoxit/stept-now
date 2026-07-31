"""In-product survey models (NPS / rating / text / select).

A `Survey` is a workspace-scoped, versioned question set delivered by the widget
as a slideout card or a modal, with the same targeting vocabulary as tours v2
(trigger / audience / schedule / frequency / priority).

`SurveyResponse` rows are append-only: a visitor who answers two questions and
then dismisses the card stores a `completed=False` row, and a later full
submission stores a *second* row with `completed=True`. Results therefore count
`completed` rows for the completion rate and for the NPS/rating math.

`contact_id` is a bare GUID (not an FK) for the same reason as `TourEvent`: the
public widget endpoint must never fail an insert on a stale contact token.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime, utcnow
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class Survey(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "surveys"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # "draft" | "live" | "paused"
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    # [{"id", "type": nps|rating|text|select, "question", "required", "options"?}] — 1..10
    questions: Mapped[list[dict[str, Any]]] = mapped_column(
        PortableJSON, default=list, nullable=False
    )
    # "modal" | "slideout"
    presentation: Mapped[str] = mapped_column(String(20), default="slideout", nullable=False)
    # {"type": "manual"} | {"type": "url_match", "url_pattern": "*/inbox*"}
    trigger: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # {"type": "all"} | {"type": "filters", "filters": [<segment-filter>, ...]}
    audience: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # {"start_at"?: iso, "end_at"?: iso} — UTC; empty = always
    schedule: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # {"type": "once"|"until_completed"|"until_dismissed"|"every_time", "cooldown_hours"?: int}
    frequency: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # {"accent": "#6366f1"}
    theme: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    thanks_message: Mapped[str] = mapped_column(
        Text, default="Thanks for the feedback!", nullable=False
    )  # markdown
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )


class SurveyResponse(WorkspaceScopedMixin, Base):
    """Append-only answers for one survey submission (partial or completed)."""

    __tablename__ = "survey_responses"
    __table_args__ = (Index("ix_survey_responses_survey_created", "survey_id", "created_at"),)

    id: Mapped[str] = pk()
    survey_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("surveys.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Bare GUID (see module docstring) — nullable for anonymous visitors.
    contact_id: Mapped[str | None] = mapped_column(GUID, index=True)
    # [{"question_id", "value": int | str}]
    answers: Mapped[list[dict[str, Any]]] = mapped_column(
        PortableJSON, default=list, nullable=False
    )
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # {"url": "https://app.example.com/inbox"}
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
