"""Outbound webhooks + their delivery log (see docs/CONTRACTS.md).

A `Webhook` subscribes to a set of event names (or "*"). The engine fan-out
creates a `WebhookDelivery` per matching event and the ``deliver_webhook``
background task POSTs the signed JSON body. `secret` is stored plain on purpose:
it is the HMAC signing key the subscriber needs to verify `X-Stept-Signature`,
not a credential we hold on their behalf.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class Webhook(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "webhooks"

    id: Mapped[str] = pk()
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Plain HMAC signing secret — generated server-side, returned to the owner.
    secret: Mapped[str] = mapped_column(String(120), nullable=False)
    # Subset of EventNames values, plus "*" for all events.
    events: Mapped[list[Any]] = mapped_column(PortableJSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))


class WebhookDelivery(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_webhook_created", "webhook_id", "created_at"),)

    id: Mapped[str] = pk()
    webhook_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("webhooks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # The exact JSON body posted: {event, workspace_id, payload, timestamp}.
    payload: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # "pending" | "success" | "failed"
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    response_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
