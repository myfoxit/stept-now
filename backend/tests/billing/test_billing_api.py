"""Billing API: readout, checkout/portal sessions, disabled mode, authz."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import stripe

from tests.billing.conftest import (
    apply_stripe_env,
    fetch_billing_row,
    seed_agent_runs,
    seed_billing_row,
)
from tests.conftest import WorkspaceCtx, bearer, signup


def mock_customer_create(monkeypatch: pytest.MonkeyPatch, customer_id: str = "cus_test_1"):
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(id=customer_id)

    monkeypatch.setattr(stripe.Customer, "create", create)
    return calls


def mock_checkout_create(
    monkeypatch: pytest.MonkeyPatch, url: str = "https://checkout.stripe.com/c/cs_test_1"
):
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(id="cs_test_1", url=url)

    monkeypatch.setattr(stripe.checkout.Session, "create", create)
    return calls


def mock_portal_create(
    monkeypatch: pytest.MonkeyPatch, url: str = "https://billing.stripe.com/p/session_1"
):
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(id="bps_test_1", url=url)

    monkeypatch.setattr(stripe.billing_portal.Session, "create", create)
    return calls


# ---------------------------------------------------------------------------
# billing disabled (self-hosted default): readout works, sessions 409
# ---------------------------------------------------------------------------


async def test_get_billing_disabled_defaults(client, workspace_ctx: WorkspaceCtx):
    response = await client.get(
        f"{workspace_ctx.base}/billing", headers=workspace_ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["billing_enabled"] is False
    assert body["plan"] == "free"
    assert body["status"] == "active"
    assert body["seats"] == 1
    assert body["member_count"] == 1
    assert body["publishable_key"] is None
    assert body["included_ai_runs"] == 0
    assert body["ai_runs_this_period"] == 0


async def test_checkout_session_conflict_when_disabled(client, workspace_ctx: WorkspaceCtx):
    response = await client.post(
        f"{workspace_ctx.base}/billing/checkout-session",
        json={"plan": "cloud"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert "not enabled" in response.json()["error"]["message"]


async def test_portal_session_conflict_when_disabled(client, workspace_ctx: WorkspaceCtx):
    response = await client.post(
        f"{workspace_ctx.base}/billing/portal-session", headers=workspace_ctx.owner_headers
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# billing enabled
# ---------------------------------------------------------------------------


async def test_get_billing_enabled_exposes_publishable_key(
    stripe_env, client, workspace_ctx: WorkspaceCtx
):
    response = await client.get(
        f"{workspace_ctx.base}/billing", headers=workspace_ctx.owner_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["billing_enabled"] is True
    assert body["publishable_key"] == "pk_test_billing_123"


async def test_checkout_session_happy_path(
    stripe_env, monkeypatch, client, workspace_ctx: WorkspaceCtx
):
    ctx = workspace_ctx
    await ctx.add_member("teammate@example.com", role="agent")  # member_count → 2
    customer_calls = mock_customer_create(monkeypatch)
    checkout_calls = mock_checkout_create(monkeypatch)

    response = await client.post(
        f"{ctx.base}/billing/checkout-session",
        json={"plan": "cloud"},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"url": "https://checkout.stripe.com/c/cs_test_1"}

    assert len(customer_calls) == 1
    assert customer_calls[0]["metadata"] == {"workspace_id": ctx.id}
    assert customer_calls[0]["email"] == "owner@example.com"

    assert len(checkout_calls) == 1
    call = checkout_calls[0]
    assert call["mode"] == "subscription"
    assert call["customer"] == "cus_test_1"
    assert call["line_items"] == [{"price": "price_cloud_123", "quantity": 2}]
    assert call["client_reference_id"] == ctx.id
    assert call["success_url"].endswith("/settings/billing?checkout=success")
    assert call["cancel_url"].endswith("/settings/billing?checkout=canceled")
    assert call["metadata"] == {"workspace_id": ctx.id, "plan": "cloud"}
    assert call["subscription_data"] == {"metadata": {"workspace_id": ctx.id}}

    row = await fetch_billing_row(ctx.id)
    assert row is not None and row.stripe_customer_id == "cus_test_1"

    # Second checkout reuses the stored customer instead of creating another.
    again = await client.post(
        f"{ctx.base}/billing/checkout-session",
        json={"plan": "business"},
        headers=ctx.owner_headers,
    )
    assert again.status_code == 200
    assert len(customer_calls) == 1
    assert checkout_calls[1]["line_items"] == [{"price": "price_business_123", "quantity": 2}]


async def test_checkout_rejects_free_plan(stripe_env, client, workspace_ctx: WorkspaceCtx):
    response = await client.post(
        f"{workspace_ctx.base}/billing/checkout-session",
        json={"plan": "free"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 422


async def test_checkout_conflict_when_price_missing(
    monkeypatch, client, workspace_ctx: WorkspaceCtx
):
    apply_stripe_env(monkeypatch, STEPT_STRIPE_PRICE_BUSINESS=None)
    response = await client.post(
        f"{workspace_ctx.base}/billing/checkout-session",
        json={"plan": "business"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 409
    assert "price" in response.json()["error"]["message"].lower()


async def test_portal_session_happy_path(
    stripe_env, monkeypatch, client, workspace_ctx: WorkspaceCtx
):
    ctx = workspace_ctx
    await seed_billing_row(ctx.id, stripe_customer_id="cus_existing", plan="cloud")
    portal_calls = mock_portal_create(monkeypatch)

    response = await client.post(f"{ctx.base}/billing/portal-session", headers=ctx.owner_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"url": "https://billing.stripe.com/p/session_1"}
    assert portal_calls[0]["customer"] == "cus_existing"
    assert portal_calls[0]["return_url"].endswith("/settings/billing")


async def test_portal_conflict_without_customer(stripe_env, client, workspace_ctx: WorkspaceCtx):
    response = await client.post(
        f"{workspace_ctx.base}/billing/portal-session", headers=workspace_ctx.owner_headers
    )
    assert response.status_code == 409


async def test_ai_runs_counts_current_month_completed_only(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    await seed_agent_runs(ctx.id, completed_this_month=2, completed_last_month=1, running=1)
    response = await client.get(f"{ctx.base}/billing", headers=ctx.owner_headers)
    assert response.status_code == 200
    assert response.json()["ai_runs_this_period"] == 2


# ---------------------------------------------------------------------------
# authz
# ---------------------------------------------------------------------------


async def test_agent_role_cannot_create_sessions(stripe_env, client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    agent_headers = await ctx.add_member("agent@example.com", role="agent")

    checkout = await client.post(
        f"{ctx.base}/billing/checkout-session", json={"plan": "cloud"}, headers=agent_headers
    )
    assert checkout.status_code == 403

    portal = await client.post(f"{ctx.base}/billing/portal-session", headers=agent_headers)
    assert portal.status_code == 403

    # Reading the billing state is member-level.
    readout = await client.get(f"{ctx.base}/billing", headers=agent_headers)
    assert readout.status_code == 200


async def test_cross_workspace_access_denied(stripe_env, client, workspace_ctx: WorkspaceCtx):
    outsider_auth = await signup(client, "outsider@example.com")
    outsider_headers = bearer(outsider_auth)
    own = await client.post(
        "/api/v1/workspaces", json={"name": "Other Corp"}, headers=outsider_headers
    )
    assert own.status_code == 201

    readout = await client.get(f"{workspace_ctx.base}/billing", headers=outsider_headers)
    assert readout.status_code == 403

    checkout = await client.post(
        f"{workspace_ctx.base}/billing/checkout-session",
        json={"plan": "cloud"},
        headers=outsider_headers,
    )
    assert checkout.status_code == 403
