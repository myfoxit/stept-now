"""Webhooks API: outbound webhook CRUD, delivery log, and test send.

The empty router is pre-registered; add routes here, never touch the registry.
All routes require webhooks:manage.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

# Side-effect import: ensures the webhook fan-out subscriber + deliver_webhook
# task are registered when the app is built, regardless of router import order.
from app.automation import engine as _automation_engine  # noqa: F401
from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.pagination import OffsetPage, clamp_limit
from app.core.permissions import Perm
from app.models.webhook import Webhook, WebhookDelivery
from app.schemas.common import Msg
from app.schemas.webhooks import (
    WebhookCreate,
    WebhookDeliveryOut,
    WebhookOut,
    WebhookUpdate,
)
from app.services import webhooks as webhooks_service

router = APIRouter()

_MANAGE = Depends(require_perm(Perm.WEBHOOKS_MANAGE))


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


def _out(webhook: Webhook) -> WebhookOut:
    return WebhookOut(
        id=webhook.id,
        url=webhook.url,
        secret=webhook.secret,
        events=list(webhook.events),
        enabled=webhook.enabled,
        description=webhook.description,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
    )


def _delivery_out(delivery: WebhookDelivery) -> WebhookDeliveryOut:
    return WebhookDeliveryOut(
        id=delivery.id,
        webhook_id=delivery.webhook_id,
        event_name=delivery.event_name,
        payload=dict(delivery.payload),
        status=delivery.status,
        response_code=delivery.response_code,
        error=delivery.error,
        attempts=delivery.attempts,
        created_at=delivery.created_at,
    )


@router.get("/webhooks", response_model=list[WebhookOut], dependencies=[_MANAGE])
async def list_webhooks(principal: Member, session: Db) -> list[WebhookOut]:
    webhooks = await webhooks_service.list_webhooks(session, principal.workspace.id)
    return [_out(w) for w in webhooks]


@router.post("/webhooks", response_model=WebhookOut, status_code=201, dependencies=[_MANAGE])
async def create_webhook(body: WebhookCreate, principal: Member, session: Db) -> WebhookOut:
    webhook = await webhooks_service.create_webhook(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        url=body.url,
        events=body.events,
        enabled=body.enabled,
        description=body.description,
    )
    return _out(webhook)


@router.get("/webhooks/{webhook_id}", response_model=WebhookOut, dependencies=[_MANAGE])
async def get_webhook(webhook_id: str, principal: Member, session: Db) -> WebhookOut:
    return _out(await webhooks_service.get_webhook(session, principal.workspace.id, webhook_id))


@router.patch("/webhooks/{webhook_id}", response_model=WebhookOut, dependencies=[_MANAGE])
async def update_webhook(
    webhook_id: str, body: WebhookUpdate, principal: Member, session: Db
) -> WebhookOut:
    webhook = await webhooks_service.update_webhook(
        session,
        principal.workspace.id,
        webhook_id,
        actor=_actor(principal),
        url=body.url,
        events=body.events,
        enabled=body.enabled,
        description=body.description,
        description_set="description" in body.model_fields_set,
    )
    return _out(webhook)


@router.delete("/webhooks/{webhook_id}", response_model=Msg, dependencies=[_MANAGE])
async def delete_webhook(webhook_id: str, principal: Member, session: Db) -> Msg:
    await webhooks_service.delete_webhook(
        session, principal.workspace.id, webhook_id, actor=_actor(principal)
    )
    return Msg(message="Webhook deleted")


@router.get(
    "/webhooks/{webhook_id}/deliveries",
    response_model=OffsetPage[WebhookDeliveryOut],
    dependencies=[_MANAGE],
)
async def list_deliveries(
    webhook_id: str,
    principal: Member,
    session: Db,
    limit: int | None = Query(None, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> OffsetPage[WebhookDeliveryOut]:
    limit = clamp_limit(limit, default=50)
    deliveries, total = await webhooks_service.list_deliveries(
        session, principal.workspace.id, webhook_id, limit=limit, offset=offset
    )
    return OffsetPage(
        items=[_delivery_out(d) for d in deliveries],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/webhooks/{webhook_id}/test", response_model=WebhookDeliveryOut, dependencies=[_MANAGE]
)
async def test_webhook(webhook_id: str, principal: Member, session: Db) -> WebhookDeliveryOut:
    delivery = await webhooks_service.send_test(session, principal.workspace.id, webhook_id)
    return _delivery_out(delivery)
