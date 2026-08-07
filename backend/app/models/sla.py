"""SLA policies: response/resolution targets, per-conversation application, breach events.

Follows Chatwoot's enterprise SLA design (docs/research/chatwoot-gaps.md §3):
- `SlaPolicy` holds the thresholds (minutes, wall-clock — business-hours awareness
  is deliberately NOT in v1).
- `AppliedSla` is the per-conversation application (one per conversation);
  status walks active → hit | missed | active_with_misses.
- `SlaEvent` records each individual breach (frt/nrt/rt); nrt events carry a
  per-waiting-episode key in `meta` so repeated misses stay distinct.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime, utcnow
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class SlaStatus(enum.StrEnum):
    ACTIVE = "active"
    HIT = "hit"
    MISSED = "missed"
    ACTIVE_WITH_MISSES = "active_with_misses"


class SlaEventType(enum.StrEnum):
    FRT = "frt"  # first response time
    NRT = "nrt"  # next response time (per waiting episode)
    RT = "rt"  # resolution time


class SlaPolicy(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "sla_policies"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Minutes; at least one threshold is required (enforced in the service).
    first_response_minutes: Mapped[int | None] = mapped_column(Integer)
    next_response_minutes: Mapped[int | None] = mapped_column(Integer)
    resolution_minutes: Mapped[int | None] = mapped_column(Integer)
    # When true the thresholds count only open minutes on the inbox's weekly
    # schedule (app.core.business_hours), so nights and weekends don't breach.
    only_during_business_hours: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AppliedSla(TimestampMixin, WorkspaceScopedMixin, Base):
    """One active SLA application per conversation; re-applying a different
    policy replaces the row in place (policy swapped, status reset)."""

    __tablename__ = "applied_slas"
    __table_args__ = (UniqueConstraint("conversation_id", name="uq_applied_slas_conversation"),)

    id: Mapped[str] = pk()
    # Plain GUID on purpose — matches csat.py's pattern for cross-domain rows.
    conversation_id: Mapped[str] = mapped_column(GUID, index=True, nullable=False)
    sla_policy_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("sla_policies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default=SlaStatus.ACTIVE, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SlaEvent(WorkspaceScopedMixin, Base):
    """A single recorded breach ("miss") for an applied SLA."""

    __tablename__ = "sla_events"

    id: Mapped[str] = pk()
    applied_sla_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("applied_slas.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(GUID, index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(10), nullable=False)  # "frt" | "nrt" | "rt"
    # nrt carries {"waiting_since": iso} — the episode key guarding one event per episode.
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
