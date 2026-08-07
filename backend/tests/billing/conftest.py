"""Billing-suite fixtures.

`app.models.billing` is imported directly (NOT via app.models.__init__ — that
registry is orchestrator-owned and gets wired later) so Base.metadata knows the
billing tables before init_db() creates the schema.

The overridden `app` fixture mounts the billing + stripe-webhook routers
exactly the way the orchestrator will (api/v1 WS prefix; /api/stripe like
/api/integrations), because those registry files must not be edited by this
wave. Everything else (client, session, workspace_ctx) comes from the root
conftest and picks the override up automatically.

No test talks to Stripe: SDK entry points are monkeypatched; webhook requests
are signed locally with the same scheme stripe.Webhook.construct_event checks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

import app.models.billing  # noqa: F401 — registers tables (registry wiring lands later)
from app.core.config import reset_settings_cache
from app.core.db import get_session_factory, utcnow, uuid7
from app.models.billing import BillingSubscription, BillingWebhookEvent

STRIPE_TEST_ENV = {
    "STEPT_STRIPE_SECRET_KEY": "sk_test_billing_123",
    "STEPT_STRIPE_WEBHOOK_SECRET": "whsec_test_billing",
    "STEPT_STRIPE_PUBLISHABLE_KEY": "pk_test_billing_123",
    "STEPT_STRIPE_PRICE_CLOUD": "price_cloud_123",
    "STEPT_STRIPE_PRICE_BUSINESS": "price_business_123",
}

WEBHOOK_SECRET = STRIPE_TEST_ENV["STEPT_STRIPE_WEBHOOK_SECRET"]


def apply_stripe_env(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> None:
    """Enable billing for this test; None drops a var (e.g. a missing price)."""
    for key, value in {**STRIPE_TEST_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    reset_settings_cache()


@pytest.fixture
def stripe_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    apply_stripe_env(monkeypatch)
    return dict(STRIPE_TEST_ENV)


@pytest.fixture
async def app():
    """create_app + the two billing mounts the orchestrator will add to the
    registries (this wave must not edit api/v1/__init__.py or main.py)."""
    from app.api.stripe_webhooks import router as stripe_webhooks_router
    from app.api.v1.billing import router as billing_router
    from app.main import create_app

    application = create_app()
    application.include_router(billing_router, prefix="/api/v1/w/{workspace_id}", tags=["billing"])
    application.include_router(stripe_webhooks_router, prefix="/api/stripe")
    async with LifespanManager(application):
        yield application


# ---------------------------------------------------------------------------
# webhook helpers
# ---------------------------------------------------------------------------


def stripe_signature(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """A Stripe-Signature header exactly as Stripe computes it (t + HMAC v1)."""
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256)
    return f"t={ts},v1={mac.hexdigest()}"


def make_event(
    event_type: str, obj: dict[str, Any], *, event_id: str | None = None
) -> dict[str, Any]:
    return {
        "id": event_id or f"evt_{uuid7()}",
        "object": "event",  # construct_event dispatches on this (v1 vs v2 thin events)
        "type": event_type,
        "data": {"object": obj},
    }


async def post_webhook(
    client: httpx.AsyncClient,
    event: dict[str, Any],
    *,
    secret: str = WEBHOOK_SECRET,
) -> httpx.Response:
    payload = json.dumps(event).encode()
    return await client.post(
        "/api/stripe/webhook",
        content=payload,
        headers={
            "Stripe-Signature": stripe_signature(payload, secret),
            "Content-Type": "application/json",
        },
    )


# ---------------------------------------------------------------------------
# db helpers (fresh sessions so API-side state is visible)
# ---------------------------------------------------------------------------


async def fetch_billing_row(workspace_id: str) -> BillingSubscription | None:
    async with get_session_factory()() as session:
        return (
            await session.execute(
                select(BillingSubscription).where(BillingSubscription.workspace_id == workspace_id)
            )
        ).scalar_one_or_none()


async def count_webhook_events(stripe_event_id: str) -> int:
    async with get_session_factory()() as session:
        rows = await session.execute(
            select(BillingWebhookEvent).where(
                BillingWebhookEvent.stripe_event_id == stripe_event_id
            )
        )
        return len(list(rows.scalars()))


async def seed_billing_row(workspace_id: str, **values: Any) -> BillingSubscription:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                select(BillingSubscription).where(BillingSubscription.workspace_id == workspace_id)
            )
        ).scalar_one_or_none()
        if row is None:
            row = BillingSubscription(workspace_id=workspace_id)
            session.add(row)
        for key, value in values.items():
            setattr(row, key, value)
        await session.commit()
        return row


async def seed_agent_runs(
    workspace_id: str,
    *,
    completed_this_month: int = 0,
    completed_last_month: int = 0,
    running: int = 0,
) -> None:
    """AgentRun rows for the usage meter (an Agent row is created to satisfy the FK)."""
    from app.models.agent import Agent
    from app.models.agent_run import AgentRun, AgentRunStatus

    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month = month_start - timedelta(days=1)
    async with get_session_factory()() as session:
        agent = Agent(workspace_id=workspace_id, name="Meter Agent")
        session.add(agent)
        await session.flush()

        def run(status: str, finished: Any) -> AgentRun:
            return AgentRun(
                workspace_id=workspace_id,
                conversation_id=uuid7(),
                agent_id=agent.id,
                status=status,
                finished_at=finished,
            )

        for _ in range(completed_this_month):
            session.add(run(AgentRunStatus.COMPLETED.value, now))
        for _ in range(completed_last_month):
            session.add(run(AgentRunStatus.COMPLETED.value, previous_month))
        for _ in range(running):
            session.add(run(AgentRunStatus.RUNNING.value, None))
        await session.commit()
