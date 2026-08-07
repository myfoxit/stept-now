"""Stripe billing state.

One `BillingSubscription` row per workspace, created lazily on first read so
self-hosted installs (no `STEPT_STRIPE_SECRET_KEY`) never notice billing
exists. All Stripe-derived fields are synced by webhooks; the row is the
local source of truth for plan/entitlement checks.

`BillingWebhookEvent` records every processed Stripe event id — the unique
constraint is the idempotency lock (Stripe redelivers aggressively).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UTCDateTime, utcnow
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class BillingPlan(enum.StrEnum):
    FREE = "free"
    CLOUD = "cloud"
    BUSINESS = "business"


class BillingStatus(enum.StrEnum):
    """Mirror of the Stripe subscription statuses we act on."""

    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"


class BillingSubscription(TimestampMixin, WorkspaceScopedMixin, Base):
    """The one billing row of a workspace (unique per workspace)."""

    __tablename__ = "billing_subscriptions"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_billing_subscriptions_workspace"),)

    id: Mapped[str] = pk()
    # Indexed for webhook resolution (invoice.* events only carry the customer).
    stripe_customer_id: Mapped[str | None] = mapped_column(String(120), index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(120), index=True)
    plan: Mapped[str] = mapped_column(String(20), default=BillingPlan.FREE, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=BillingStatus.ACTIVE, nullable=False)
    seats: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class BillingWebhookEvent(Base):
    """Processed Stripe webhook event ids (idempotency ledger).

    Deliberately not workspace-scoped: an event may fail to resolve to a
    workspace and must still be remembered as seen.
    """

    __tablename__ = "billing_webhook_events"

    id: Mapped[str] = pk()
    stripe_event_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(120), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
