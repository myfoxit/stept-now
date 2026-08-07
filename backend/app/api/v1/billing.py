"""Billing API: plan/usage readout + Stripe Checkout/Portal session creation.

Any member can read the billing state; starting a checkout or opening the
billing portal needs workspace:manage. On self-hosted installs (billing
disabled) the readout still works — with ``billing_enabled: false`` — while
the session endpoints answer 409.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.schemas.billing import (
    BillingOut,
    CheckoutSessionCreate,
    CheckoutSessionOut,
    PortalSessionOut,
)
from app.services import billing as billing_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


def require_plan_feature(feature: billing_service.Feature):
    """Router dependency: 403 when a hosted workspace's plan lacks the feature.

    Self-hosted installs (no Stripe key) are always entitled — the check is a
    no-op there, so OSS behavior is unchanged. Compose it AFTER require_perm so
    permission errors (who you are) outrank plan errors (what you pay for).
    """

    async def checker(principal: Member, session: Db) -> None:
        await billing_service.require_feature(session, principal.workspace.id, feature)

    return checker


@router.get("/billing", response_model=BillingOut)
async def get_billing(principal: Member, session: Db) -> BillingOut:
    return await billing_service.get_billing(session, principal.workspace.id)


@router.post(
    "/billing/checkout-session",
    response_model=CheckoutSessionOut,
    dependencies=[Depends(require_perm(Perm.WORKSPACE_MANAGE))],
)
async def create_checkout_session(
    body: CheckoutSessionCreate, principal: Member, session: Db
) -> CheckoutSessionOut:
    url = await billing_service.create_checkout_session(
        session,
        principal.workspace,
        actor=_actor(principal),
        plan=body.plan,
        customer_email=principal.user.email if principal.user is not None else None,
    )
    return CheckoutSessionOut(url=url)


@router.post(
    "/billing/portal-session",
    response_model=PortalSessionOut,
    dependencies=[Depends(require_perm(Perm.WORKSPACE_MANAGE))],
)
async def create_portal_session(principal: Member, session: Db) -> PortalSessionOut:
    url = await billing_service.create_portal_session(
        session, principal.workspace, actor=_actor(principal)
    )
    return PortalSessionOut(url=url)
