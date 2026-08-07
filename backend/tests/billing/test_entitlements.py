"""Entitlement resolver: disabled ⇒ everything entitled; enabled ⇒ plan matrix."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory, uuid7
from app.core.errors import ForbiddenError
from app.models.billing import BillingSubscription
from app.models.workspace import Workspace
from app.services.billing import PLAN_CATALOG, Feature, billing_enabled, require_feature
from tests.billing.conftest import apply_stripe_env

ALL_FEATURES = tuple(Feature)


async def make_workspace(session: AsyncSession, *, plan: str | None = None) -> Workspace:
    workspace = Workspace(name="Billing WS", slug=f"billing-{uuid7()}", settings={})
    session.add(workspace)
    await session.flush()
    if plan is not None:
        session.add(BillingSubscription(workspace_id=workspace.id, plan=plan))
        await session.flush()
    return workspace


def test_catalog_matrix_matches_the_documented_mapping():
    assert PLAN_CATALOG["free"].features == frozenset()
    assert PLAN_CATALOG["cloud"].features == frozenset(Feature)  # cloud lacks none in v1
    assert PLAN_CATALOG["business"].features == frozenset(Feature)
    assert PLAN_CATALOG["cloud"].included_ai_runs == 500
    assert PLAN_CATALOG["business"].included_ai_runs == 2000
    assert PLAN_CATALOG["cloud"].price_per_seat_usd == 19
    assert PLAN_CATALOG["business"].price_per_seat_usd == 49


async def test_billing_disabled_entitles_everything(db_only: AsyncSession):
    assert billing_enabled() is False
    workspace = await make_workspace(db_only)  # no billing row at all
    for feature in ALL_FEATURES:
        await require_feature(db_only, workspace.id, feature)  # must not raise


async def test_free_plan_lacks_all_gated_features(monkeypatch, db_only: AsyncSession):
    apply_stripe_env(monkeypatch)
    assert billing_enabled() is True
    workspace = await make_workspace(db_only)  # row is created lazily on free
    for feature in ALL_FEATURES:
        with pytest.raises(ForbiddenError, match="Upgrade required"):
            await require_feature(db_only, workspace.id, feature)


@pytest.mark.parametrize("plan", ["cloud", "business"])
async def test_paid_plans_are_fully_entitled(monkeypatch, db_only: AsyncSession, plan: str):
    apply_stripe_env(monkeypatch)
    workspace = await make_workspace(db_only, plan=plan)
    for feature in ALL_FEATURES:
        await require_feature(db_only, workspace.id, feature)  # must not raise


# ---------------------------------------------------------------------------
# router wiring: the gates actually guard the endpoints over HTTP
# ---------------------------------------------------------------------------

ROLE_BODY = {"name": "Support Lead", "description": "", "permissions": ["conversations:read"]}


async def test_free_hosted_workspace_gets_402_style_403_on_gated_routes(
    monkeypatch, client, workspace_ctx
):
    apply_stripe_env(monkeypatch)

    create_role = await client.post(
        f"{workspace_ctx.base}/roles", json=ROLE_BODY, headers=workspace_ctx.owner_headers
    )
    assert create_role.status_code == 403
    assert "Upgrade required" in create_role.json()["error"]["message"]

    audit_read = await client.get(
        f"{workspace_ctx.base}/audit", headers=workspace_ctx.owner_headers
    )
    assert audit_read.status_code == 403
    assert "Upgrade required" in audit_read.json()["error"]["message"]

    sla_create = await client.post(
        f"{workspace_ctx.base}/slas",
        json={"name": "Gold", "first_response_minutes": 30},
        headers=workspace_ctx.owner_headers,
    )
    assert sla_create.status_code == 403
    assert "Upgrade required" in sla_create.json()["error"]["message"]


async def test_self_hosted_keeps_everything_ungated(client, workspace_ctx):
    assert billing_enabled() is False
    create_role = await client.post(
        f"{workspace_ctx.base}/roles", json=ROLE_BODY, headers=workspace_ctx.owner_headers
    )
    assert create_role.status_code == 201, create_role.text
    audit_read = await client.get(
        f"{workspace_ctx.base}/audit", headers=workspace_ctx.owner_headers
    )
    assert audit_read.status_code == 200


async def test_paid_hosted_workspace_passes_the_gates(monkeypatch, client, workspace_ctx):
    apply_stripe_env(monkeypatch)
    async with get_session_factory()() as db:
        db.add(BillingSubscription(workspace_id=workspace_ctx.id, plan="cloud"))
        await db.commit()
    create_role = await client.post(
        f"{workspace_ctx.base}/roles", json=ROLE_BODY, headers=workspace_ctx.owner_headers
    )
    assert create_role.status_code == 201, create_role.text


async def test_unknown_plan_fails_open(monkeypatch, db_only: AsyncSession):
    """A corrupt/legacy plan value must never lock out a paying customer."""
    apply_stripe_env(monkeypatch)
    workspace = await make_workspace(db_only, plan="enterprise-legacy")
    for feature in ALL_FEATURES:
        await require_feature(db_only, workspace.id, feature)  # must not raise


async def test_require_feature_creates_the_free_row_lazily(monkeypatch, db_only: AsyncSession):
    apply_stripe_env(monkeypatch)
    workspace = await make_workspace(db_only)
    with pytest.raises(ForbiddenError):
        await require_feature(db_only, workspace.id, Feature.AUDIT_LOG)
    row = (
        await db_only.execute(
            select(BillingSubscription).where(BillingSubscription.workspace_id == workspace.id)
        )
    ).scalar_one()
    assert row.plan == "free"
