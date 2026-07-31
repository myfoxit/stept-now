"""Automation rules: event-triggered condition→action rules (see docs/CONTRACTS.md).

An `AutomationRule` subscribes to one domain event. When that event fires the
engine (`app/automation/engine.py`) hydrates a context from the payload ids,
checks the rule's AND-ed conditions, and runs its ordered actions through the
conversation / notification services and webhook delivery.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class AutomationRule(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "automation_rules"
    __table_args__ = (
        Index("ix_automation_rules_ws_event_enabled", "workspace_id", "event", "enabled"),
    )

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # One of the engine-subscribable EventNames values (conversation.created,
    # message.created, conversation.status_changed, csat.submitted, contact.created).
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    # [{"field","op","value"}] evaluated AND-wise; empty list matches everything.
    conditions: Mapped[list[Any]] = mapped_column(PortableJSON, default=list, nullable=False)
    # [{"type","params"}] executed in order.
    actions: Mapped[list[Any]] = mapped_column(PortableJSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Evaluation order within a (workspace, event); lower runs first.
    ord: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
