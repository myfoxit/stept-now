"""Stripe subscription billing (v1).

THE RULE: when ``settings.stripe_secret_key`` is unset (every self-hosted
install) billing is disabled and EVERYTHING is entitled — no gating, no Stripe
calls, no behavior change for OSS. All entry points check ``billing_enabled()``
first.

Plan catalog (v1):

    free      $0/seat    0 included AI runs   (1-seat limit is NOT enforced in v1)
    cloud     $19/seat   500 included AI runs / month
    business  $49/seat   2000 included AI runs / month

Entitlement mapping (v1). The landing page sells SSO/SAML, audit logs, custom
roles, white-label widget and SLA management under "Business". Only the three
that exist in the product today are gateable, so the ``Feature`` enum is:

    Feature.CUSTOM_ROLES    → custom roles     (api/v1/members role endpoints)
    Feature.AUDIT_LOG       → audit log        (api/v1/audit)
    Feature.SLA_MANAGEMENT  → SLA management   (api/v1/slas)

Matrix: the hosted *free* plan lacks all three; **cloud lacks none in v1** —
any paid plan unlocks everything. Tightening cloud vs business waits until the
real Business differentiators (SSO, white-label) exist. ``require_feature`` is
exported for later wiring; no router calls it yet.

Sync Stripe SDK calls are pushed off the event loop with ``asyncio.to_thread``;
``stripe.api_key`` is set immediately before each call. Secrets are never
logged.
"""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import stripe
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import utcnow
from app.core.errors import ConflictError, ForbiddenError, ValidationFailure
from app.core.events import Actor
from app.core.logging import log
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.billing import BillingPlan, BillingStatus, BillingSubscription, BillingWebhookEvent
from app.models.workspace import Membership, Workspace
from app.schemas.billing import BillingOut
from app.services import audit

logger = log("billing")


class Feature(enum.StrEnum):
    CUSTOM_ROLES = "custom_roles"
    AUDIT_LOG = "audit_log"
    SLA_MANAGEMENT = "sla_management"


FEATURE_LABELS: dict[Feature, str] = {
    Feature.CUSTOM_ROLES: "Custom roles",
    Feature.AUDIT_LOG: "Audit log",
    Feature.SLA_MANAGEMENT: "SLA management",
}


@dataclass(frozen=True)
class PlanDef:
    key: str
    name: str
    price_per_seat_usd: int
    included_ai_runs: int
    features: frozenset[Feature]


_ALL_FEATURES: frozenset[Feature] = frozenset(Feature)

PLAN_CATALOG: dict[str, PlanDef] = {
    BillingPlan.FREE: PlanDef(
        key=BillingPlan.FREE,
        name="Free",
        price_per_seat_usd=0,
        included_ai_runs=0,
        features=frozenset(),
    ),
    BillingPlan.CLOUD: PlanDef(
        key=BillingPlan.CLOUD,
        name="Cloud",
        price_per_seat_usd=19,
        included_ai_runs=500,
        features=_ALL_FEATURES,  # cloud lacks none in v1 — see module docstring
    ),
    BillingPlan.BUSINESS: PlanDef(
        key=BillingPlan.BUSINESS,
        name="Business",
        price_per_seat_usd=49,
        included_ai_runs=2000,
        features=_ALL_FEATURES,
    ),
}


def billing_enabled() -> bool:
    """Billing exists only when a Stripe secret key is configured."""
    return bool(get_settings().stripe_secret_key)


def _require_billing_enabled() -> Settings:
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise ConflictError("Billing is not enabled on this instance")
    return settings


def _price_for_plan(settings: Settings, plan: str) -> str:
    price = (
        settings.stripe_price_cloud if plan == BillingPlan.CLOUD else settings.stripe_price_business
    )
    if not price:
        raise ConflictError(f"No Stripe price is configured for the {plan} plan")
    return price


def _plan_from_price(price_id: str | None) -> str | None:
    if not price_id:
        return None
    settings = get_settings()
    if price_id == settings.stripe_price_cloud:
        return BillingPlan.CLOUD.value
    if price_id == settings.stripe_price_business:
        return BillingPlan.BUSINESS.value
    return None


# ---------------------------------------------------------------------------
# readout
# ---------------------------------------------------------------------------


async def _get_row(session: AsyncSession, workspace_id: str) -> BillingSubscription:
    """The workspace's billing row, created lazily on the free plan."""
    row = (
        await session.execute(
            select(BillingSubscription).where(BillingSubscription.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = BillingSubscription(workspace_id=workspace_id)
        session.add(row)
        await session.flush()
    return row


async def _member_count(session: AsyncSession, workspace_id: str) -> int:
    result = await session.execute(
        select(func.count()).select_from(Membership).where(Membership.workspace_id == workspace_id)
    )
    return int(result.scalar_one())


async def _ai_runs_this_period(session: AsyncSession, workspace_id: str) -> int:
    """Completed AgentRuns in the current calendar month (the usage meter)."""
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        select(func.count())
        .select_from(AgentRun)
        .where(
            AgentRun.workspace_id == workspace_id,
            AgentRun.status == AgentRunStatus.COMPLETED.value,
            AgentRun.finished_at >= month_start,
        )
    )
    return int(result.scalar_one())


async def get_billing(session: AsyncSession, workspace_id: str) -> BillingOut:
    settings = get_settings()
    enabled = billing_enabled()
    row = await _get_row(session, workspace_id)
    plan = PLAN_CATALOG.get(row.plan, PLAN_CATALOG[BillingPlan.FREE])
    return BillingOut(
        plan=row.plan,
        status=row.status,
        seats=row.seats,
        member_count=await _member_count(session, workspace_id),
        current_period_end=row.current_period_end,
        cancel_at_period_end=row.cancel_at_period_end,
        ai_runs_this_period=await _ai_runs_this_period(session, workspace_id),
        included_ai_runs=plan.included_ai_runs,
        billing_enabled=enabled,
        publishable_key=settings.stripe_publishable_key if enabled else None,
    )


# ---------------------------------------------------------------------------
# checkout + portal
# ---------------------------------------------------------------------------


async def _ensure_customer(
    session: AsyncSession,
    workspace: Workspace,
    row: BillingSubscription,
    *,
    settings: Settings,
    email: str | None,
) -> str:
    if row.stripe_customer_id:
        return row.stripe_customer_id
    stripe.api_key = settings.stripe_secret_key
    params: dict[str, Any] = {"name": workspace.name, "metadata": {"workspace_id": workspace.id}}
    if email:
        params["email"] = email
    customer = await asyncio.to_thread(lambda: stripe.Customer.create(**params))
    row.stripe_customer_id = str(customer.id)
    await session.flush()
    return row.stripe_customer_id


async def create_checkout_session(
    session: AsyncSession,
    workspace: Workspace,
    *,
    actor: Actor,
    plan: str,
    customer_email: str | None = None,
) -> str:
    """Start a Stripe Checkout (mode=subscription) and return its URL."""
    settings = _require_billing_enabled()
    if plan not in (BillingPlan.CLOUD, BillingPlan.BUSINESS):
        raise ValidationFailure("Choose the cloud or business plan")
    price = _price_for_plan(settings, plan)
    row = await _get_row(session, workspace.id)
    customer_id = await _ensure_customer(
        session, workspace, row, settings=settings, email=customer_email
    )
    seats = await _member_count(session, workspace.id)
    base = settings.app_base_url.rstrip("/")
    stripe.api_key = settings.stripe_secret_key
    checkout = await asyncio.to_thread(
        lambda: stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price, "quantity": seats}],
            success_url=f"{base}/settings/billing?checkout=success",
            cancel_url=f"{base}/settings/billing?checkout=canceled",
            client_reference_id=workspace.id,
            # Belt and braces: checkout.session.completed carries no line items
            # unless expanded, so the plan also rides in metadata.
            metadata={"workspace_id": workspace.id, "plan": plan},
            subscription_data={"metadata": {"workspace_id": workspace.id}},
        )
    )
    url = checkout.url
    if not url:
        raise ConflictError("Stripe did not return a checkout URL")
    await audit.record(
        session,
        workspace.id,
        actor=actor,
        action="billing.checkout_session.create",
        target_type="billing_subscription",
        target_id=row.id,
        meta={"plan": plan, "seats": seats},
    )
    return str(url)


async def create_portal_session(
    session: AsyncSession, workspace: Workspace, *, actor: Actor
) -> str:
    """Open the Stripe Billing Portal for the workspace's customer."""
    settings = _require_billing_enabled()
    row = await _get_row(session, workspace.id)
    customer_id = row.stripe_customer_id
    if not customer_id:
        raise ConflictError("This workspace has no billing account yet — upgrade first")
    base = settings.app_base_url.rstrip("/")
    stripe.api_key = settings.stripe_secret_key
    portal = await asyncio.to_thread(
        lambda: stripe.billing_portal.Session.create(
            customer=customer_id, return_url=f"{base}/settings/billing"
        )
    )
    await audit.record(
        session,
        workspace.id,
        actor=actor,
        action="billing.portal_session.create",
        target_type="billing_subscription",
        target_id=row.id,
    )
    return str(portal.url)


# ---------------------------------------------------------------------------
# webhooks
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_item(container: Any) -> dict[str, Any]:
    """``{"data": [item, …]}`` → first item (Stripe list shape), else {}."""
    data = _as_dict(container).get("data")
    if isinstance(data, list) and data:
        return _as_dict(data[0])
    return {}


async def _row_for_workspace(
    session: AsyncSession, workspace_id: str
) -> BillingSubscription | None:
    """Billing row for an id coming from Stripe — verified against real tenants."""
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        return None
    return await _get_row(session, workspace_id)


async def _row_by_column(
    session: AsyncSession, column: Any, value: str
) -> BillingSubscription | None:
    return (
        await session.execute(select(BillingSubscription).where(column == value))
    ).scalar_one_or_none()


async def _resolve_subscription_row(
    session: AsyncSession, obj: dict[str, Any]
) -> BillingSubscription | None:
    """Workspace resolution for subscription objects: metadata → stored
    subscription id → stored customer id."""
    meta_ws = _as_dict(obj.get("metadata")).get("workspace_id")
    if meta_ws:
        row = await _row_for_workspace(session, str(meta_ws))
        if row is not None:
            return row
    sub_id = obj.get("id")
    if sub_id:
        row = await _row_by_column(session, BillingSubscription.stripe_subscription_id, str(sub_id))
        if row is not None:
            return row
    customer = obj.get("customer")
    if customer:
        return await _row_by_column(session, BillingSubscription.stripe_customer_id, str(customer))
    return None


async def _record_transition(
    session: AsyncSession, row: BillingSubscription, action: str, meta: dict[str, Any]
) -> None:
    await session.flush()
    await audit.record(
        session,
        row.workspace_id,
        actor=Actor.system(),
        action=action,
        target_type="billing_subscription",
        target_id=row.id,
        meta=meta,
    )


async def _on_checkout_completed(session: AsyncSession, obj: dict[str, Any]) -> None:
    workspace_id = obj.get("client_reference_id") or _as_dict(obj.get("metadata")).get(
        "workspace_id"
    )
    if not workspace_id:
        logger.warning("checkout.session.completed without a workspace reference — skipped")
        return
    row = await _row_for_workspace(session, str(workspace_id))
    if row is None:
        logger.warning("checkout.session.completed for unknown workspace — skipped")
        return
    if obj.get("customer"):
        row.stripe_customer_id = str(obj["customer"])
    if obj.get("subscription"):
        row.stripe_subscription_id = str(obj["subscription"])
    row.status = BillingStatus.ACTIVE.value
    # Plan from the line-item price when line_items were expanded; the metadata
    # we stamped at session creation is the fallback (Stripe's default payload
    # has no line items). Seats likewise; the subscription.updated event that
    # follows every checkout corrects both authoritatively.
    item = _first_item(obj.get("line_items"))
    plan = _plan_from_price(_as_dict(item.get("price")).get("id"))
    if plan is None:
        meta_plan = _as_dict(obj.get("metadata")).get("plan")
        if meta_plan in PLAN_CATALOG:
            plan = str(meta_plan)
    if plan is not None:
        row.plan = plan
    quantity = item.get("quantity")
    row.seats = int(quantity) if quantity else await _member_count(session, row.workspace_id)
    await _record_transition(
        session,
        row,
        "billing.subscription.activated",
        {"plan": row.plan, "seats": row.seats},
    )


async def _on_subscription_updated(session: AsyncSession, obj: dict[str, Any]) -> None:
    row = await _resolve_subscription_row(session, obj)
    if row is None:
        logger.warning("subscription event did not resolve to a workspace — skipped")
        return
    if obj.get("id"):
        row.stripe_subscription_id = str(obj["id"])
    if obj.get("customer"):
        row.stripe_customer_id = str(obj["customer"])
    item = _first_item(obj.get("items"))
    plan = _plan_from_price(_as_dict(item.get("price")).get("id"))
    if plan is not None:
        row.plan = plan
    if item.get("quantity"):
        row.seats = int(item["quantity"])
    status = obj.get("status")
    if status:
        row.status = str(status)
    # Newer Stripe API versions moved current_period_end onto the item.
    period_end = obj.get("current_period_end") or item.get("current_period_end")
    if period_end:
        row.current_period_end = datetime.fromtimestamp(int(period_end), tz=UTC)
    row.cancel_at_period_end = bool(obj.get("cancel_at_period_end", row.cancel_at_period_end))
    await _record_transition(
        session,
        row,
        "billing.subscription.updated",
        {"plan": row.plan, "status": row.status, "seats": row.seats},
    )


async def _on_subscription_deleted(session: AsyncSession, obj: dict[str, Any]) -> None:
    row = await _resolve_subscription_row(session, obj)
    if row is None:
        return
    row.plan = BillingPlan.FREE.value
    row.status = BillingStatus.CANCELED.value
    row.cancel_at_period_end = False
    row.stripe_subscription_id = None  # keep the customer id for future re-subscribes
    await _record_transition(session, row, "billing.subscription.canceled", {})


async def _on_payment_failed(session: AsyncSession, obj: dict[str, Any]) -> None:
    row: BillingSubscription | None = None
    sub_id = obj.get("subscription")
    if sub_id:
        row = await _row_by_column(session, BillingSubscription.stripe_subscription_id, str(sub_id))
    if row is None and obj.get("customer"):
        row = await _row_by_column(
            session, BillingSubscription.stripe_customer_id, str(obj["customer"])
        )
    if row is None:
        return
    row.status = BillingStatus.PAST_DUE.value
    await _record_transition(session, row, "billing.payment_failed", {"plan": row.plan})


async def apply_webhook_event(session: AsyncSession, event: dict[str, Any]) -> bool:
    """Apply one (signature-verified) Stripe event. Returns False on replays.

    Idempotency: the ``BillingWebhookEvent`` unique insert is the lock — a
    replayed event id is recorded exactly once and applied exactly once. Two
    *concurrent* deliveries of the same id both pass the pre-check; the loser's
    flush hits the unique constraint, the request 500s, and Stripe's retry then
    sees the recorded id — still exactly-once.
    """
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    if not event_id or not event_type:
        raise ValidationFailure("Malformed Stripe event")
    seen = (
        await session.execute(
            select(BillingWebhookEvent.id).where(BillingWebhookEvent.stripe_event_id == event_id)
        )
    ).scalar_one_or_none()
    if seen is not None:
        return False
    session.add(BillingWebhookEvent(stripe_event_id=event_id, type=event_type))
    await session.flush()

    obj = _as_dict(_as_dict(event.get("data")).get("object"))
    if event_type == "checkout.session.completed":
        await _on_checkout_completed(session, obj)
    elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
        await _on_subscription_updated(session, obj)
    elif event_type == "customer.subscription.deleted":
        await _on_subscription_deleted(session, obj)
    elif event_type == "invoice.payment_failed":
        await _on_payment_failed(session, obj)
    # Unknown event types are recorded (idempotency) and deliberately ignored.
    return True


# ---------------------------------------------------------------------------
# entitlements
# ---------------------------------------------------------------------------


async def require_feature(session: AsyncSession, workspace_id: str, feature: Feature) -> None:
    """Raise ForbiddenError when a *hosted* workspace's plan lacks the feature.

    Self-hosted (billing disabled): always entitled, never raises. Plan status
    is deliberately ignored in v1 — past_due keeps its features (dunning grace);
    cancellation flips the plan to free via webhook, which is what revokes.
    Not wired into any router yet; exported for later enforcement.
    """
    if not billing_enabled():
        return
    row = await _get_row(session, workspace_id)
    plan = PLAN_CATALOG.get(row.plan)
    # An unrecognized plan value must never lock out a paying customer: fail open.
    features = plan.features if plan is not None else _ALL_FEATURES
    if feature in features:
        return
    label = FEATURE_LABELS.get(feature, feature.value)
    raise ForbiddenError(f"Upgrade required: {label} is available on paid plans")
