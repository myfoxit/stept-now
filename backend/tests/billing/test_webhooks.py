"""Stripe webhook endpoint: signature verification, idempotency, lifecycle sync."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from tests.billing.conftest import (
    count_webhook_events,
    fetch_billing_row,
    make_event,
    post_webhook,
    seed_billing_row,
    stripe_signature,
)
from tests.conftest import WorkspaceCtx

PERIOD_END = 1790000000  # 2026-09-21T15:33:20Z


def checkout_completed(workspace_id: str, **overrides: Any) -> dict[str, Any]:
    obj = {
        "id": "cs_test_1",
        "object": "checkout.session",
        "client_reference_id": workspace_id,
        "customer": "cus_hook_1",
        "subscription": "sub_hook_1",
        "metadata": {"workspace_id": workspace_id, "plan": "cloud"},
        "line_items": {
            "data": [{"price": {"id": "price_cloud_123"}, "quantity": 3}],
        },
        **overrides,
    }
    return make_event("checkout.session.completed", obj)


def subscription_updated(**overrides: Any) -> dict[str, Any]:
    obj = {
        "id": "sub_hook_1",
        "object": "subscription",
        "customer": "cus_hook_1",
        "status": "active",
        "cancel_at_period_end": False,
        "current_period_end": PERIOD_END,
        "metadata": {},
        "items": {"data": [{"price": {"id": "price_cloud_123"}, "quantity": 3}]},
        **overrides,
    }
    return make_event("customer.subscription.updated", obj)


# ---------------------------------------------------------------------------
# transport: signature + configuration
# ---------------------------------------------------------------------------


async def test_invalid_signature_is_400(stripe_env, client):
    payload = json.dumps(make_event("checkout.session.completed", {})).encode()

    garbage = await client.post(
        "/api/stripe/webhook",
        content=payload,
        headers={"Stripe-Signature": "t=1,v1=deadbeef", "Content-Type": "application/json"},
    )
    assert garbage.status_code == 400

    wrong_secret = await client.post(
        "/api/stripe/webhook",
        content=payload,
        headers={
            "Stripe-Signature": stripe_signature(payload, "whsec_other"),
            "Content-Type": "application/json",
        },
    )
    assert wrong_secret.status_code == 400

    missing_header = await client.post(
        "/api/stripe/webhook", content=payload, headers={"Content-Type": "application/json"}
    )
    assert missing_header.status_code == 400


async def test_webhook_unconfigured_is_400(client):
    response = await client.post("/api/stripe/webhook", content=b"{}")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# event application
# ---------------------------------------------------------------------------


async def test_checkout_completed_activates_subscription(
    stripe_env, client, workspace_ctx: WorkspaceCtx
):
    response = await post_webhook(client, checkout_completed(workspace_ctx.id))
    assert response.status_code == 200, response.text
    assert response.json() == {"received": True}

    row = await fetch_billing_row(workspace_ctx.id)
    assert row is not None
    assert row.stripe_customer_id == "cus_hook_1"
    assert row.stripe_subscription_id == "sub_hook_1"
    assert row.plan == "cloud"  # from the line-item price id
    assert row.status == "active"
    assert row.seats == 3  # from the line-item quantity


async def test_checkout_completed_metadata_fallback(
    stripe_env, client, workspace_ctx: WorkspaceCtx
):
    """Stripe's default payload has no line items — plan falls back to the
    metadata stamped at session creation, seats to the member count."""
    event = checkout_completed(workspace_ctx.id, line_items=None)
    response = await post_webhook(client, event)
    assert response.status_code == 200

    row = await fetch_billing_row(workspace_ctx.id)
    assert row is not None
    assert row.plan == "cloud"  # metadata fallback
    assert row.seats == 1  # member count fallback (owner only)


async def test_webhook_idempotent_by_event_id(stripe_env, client, workspace_ctx: WorkspaceCtx):
    await seed_billing_row(
        workspace_ctx.id, stripe_subscription_id="sub_hook_1", stripe_customer_id="cus_hook_1"
    )
    first = subscription_updated(
        metadata={"workspace_id": workspace_ctx.id},
        items={"data": [{"price": {"id": "price_cloud_123"}, "quantity": 5}]},
    )
    assert (await post_webhook(client, first)).status_code == 200
    row = await fetch_billing_row(workspace_ctx.id)
    assert row is not None and row.seats == 5

    # Same event id, different body: must not re-apply.
    replay = subscription_updated(
        metadata={"workspace_id": workspace_ctx.id},
        items={"data": [{"price": {"id": "price_cloud_123"}, "quantity": 9}]},
    )
    replay["id"] = first["id"]
    response = await post_webhook(client, replay)
    assert response.status_code == 200
    assert response.json() == {"received": True}

    row = await fetch_billing_row(workspace_ctx.id)
    assert row is not None and row.seats == 5
    assert await count_webhook_events(first["id"]) == 1


async def test_subscription_lifecycle(stripe_env, client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx

    # 1. Checkout completes → active on cloud.
    assert (await post_webhook(client, checkout_completed(ctx.id))).status_code == 200

    # 2. Upgrade synced via subscription.updated — resolved by the STORED
    #    subscription id (metadata deliberately empty).
    upgraded = subscription_updated(
        items={"data": [{"price": {"id": "price_business_123"}, "quantity": 4}]},
        cancel_at_period_end=True,
    )
    assert (await post_webhook(client, upgraded)).status_code == 200
    row = await fetch_billing_row(ctx.id)
    assert row is not None
    assert row.plan == "business"
    assert row.seats == 4
    assert row.cancel_at_period_end is True
    assert row.current_period_end == datetime.fromtimestamp(PERIOD_END, tz=UTC)

    # 3. A payment fails → past_due (resolved by customer id).
    failed = make_event(
        "invoice.payment_failed",
        {"id": "in_1", "object": "invoice", "customer": "cus_hook_1"},
    )
    assert (await post_webhook(client, failed)).status_code == 200
    row = await fetch_billing_row(ctx.id)
    assert row is not None and row.status == "past_due"

    # 4. Subscription deleted → back to free, canceled, subscription id cleared.
    deleted = make_event(
        "customer.subscription.deleted",
        {
            "id": "sub_hook_1",
            "object": "subscription",
            "customer": "cus_hook_1",
            "metadata": {"workspace_id": ctx.id},
        },
    )
    assert (await post_webhook(client, deleted)).status_code == 200
    row = await fetch_billing_row(ctx.id)
    assert row is not None
    assert row.plan == "free"
    assert row.status == "canceled"
    assert row.stripe_subscription_id is None
    assert row.stripe_customer_id == "cus_hook_1"  # kept for re-subscribing


async def test_unknown_event_type_is_acknowledged(stripe_env, client):
    response = await post_webhook(client, make_event("customer.created", {"id": "cus_x"}))
    assert response.status_code == 200
    assert response.json() == {"received": True}


async def test_unresolvable_workspace_is_skipped(stripe_env, client, workspace_ctx: WorkspaceCtx):
    event = subscription_updated(
        id="sub_unknown",
        customer="cus_unknown",
        metadata={"workspace_id": "not-a-workspace"},
    )
    response = await post_webhook(client, event)
    assert response.status_code == 200

    row = await fetch_billing_row(workspace_ctx.id)
    assert row is None or row.stripe_subscription_id != "sub_unknown"
