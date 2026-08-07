"""Pydantic schemas for billing."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class BillingOut(BaseModel):
    """Plan + usage readout for the settings billing panel.

    `billing_enabled` is False on self-hosted installs (no Stripe key
    configured) — the frontend hides the whole panel body then.
    """

    plan: str
    status: str
    seats: int
    member_count: int
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    ai_runs_this_period: int
    included_ai_runs: int
    billing_enabled: bool
    publishable_key: str | None = None


class CheckoutSessionCreate(BaseModel):
    """Only paid plans are checkout-able; downgrades go through the portal."""

    plan: Literal["cloud", "business"]


class CheckoutSessionOut(BaseModel):
    url: str


class PortalSessionOut(BaseModel):
    url: str
