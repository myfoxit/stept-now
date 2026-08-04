"""Outbound webhooks: CRUD, event fan-out, and signed background delivery.

Fan-out (called from the engine's "*" subscriber) creates a `WebhookDelivery`
row per matching webhook and enqueues ``deliver_webhook``. The task POSTs the
stored JSON body with an HMAC-SHA256 `X-Stept-Signature`; non-2xx / transport
errors raise so the queue retries (3x), and the last attempt marks the delivery
failed. Signing over the exact stored bytes keeps the signature stable across
retries.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope, utcnow
from app.core.errors import NotFoundError
from app.core.events import Actor, Event
from app.core.logging import log
from app.core.net import UnsafeUrlError, assert_public_url
from app.core.queue import MAX_ATTEMPTS, TaskContext, enqueue, task
from app.core.security import new_token
from app.models.webhook import Webhook, WebhookDelivery
from app.services import audit

logger = log("webhooks")

DELIVERY_TIMEOUT_SECONDS = 10.0


# ---------------------------------------------------------------------------
# signing + body
# ---------------------------------------------------------------------------


def sign_payload(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex digest of the raw body — the `X-Stept-Signature` value."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _body(event_name: str, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": event_name,
        "workspace_id": workspace_id,
        "payload": payload,
        "timestamp": utcnow().isoformat(),
    }


def _encode_body(body: dict[str, Any]) -> bytes:
    """Deterministic bytes for both signing and posting."""
    return json.dumps(body, separators=(",", ":"), sort_keys=True, default=str).encode()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def get_webhook(session: AsyncSession, workspace_id: str, webhook_id: str) -> Webhook:
    webhook = await session.get(Webhook, webhook_id)
    if webhook is None or webhook.workspace_id != workspace_id:
        raise NotFoundError("Webhook not found")
    return webhook


async def list_webhooks(session: AsyncSession, workspace_id: str) -> list[Webhook]:
    result = await session.execute(
        select(Webhook)
        .where(Webhook.workspace_id == workspace_id)
        .order_by(Webhook.created_at, Webhook.id)
    )
    return list(result.scalars())


async def create_webhook(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    url: str,
    events: list[str],
    enabled: bool = True,
    description: str | None = None,
) -> Webhook:
    webhook = Webhook(
        workspace_id=workspace_id,
        url=url,
        secret=new_token(24),
        events=events,
        enabled=enabled,
        description=description,
    )
    session.add(webhook)
    await session.flush()
    _invalidate_cache(workspace_id)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="webhook.create",
        target_type="webhook",
        target_id=webhook.id,
        meta={"url": url, "events": events},
    )
    return webhook


async def update_webhook(
    session: AsyncSession,
    workspace_id: str,
    webhook_id: str,
    *,
    actor: Actor,
    url: str | None = None,
    events: list[str] | None = None,
    enabled: bool | None = None,
    description: str | None = None,
    description_set: bool = False,
) -> Webhook:
    webhook = await get_webhook(session, workspace_id, webhook_id)
    if url is not None:
        webhook.url = url
    if events is not None:
        webhook.events = events
    if enabled is not None:
        webhook.enabled = enabled
    if description_set:
        webhook.description = description
    await session.flush()
    _invalidate_cache(workspace_id)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="webhook.update",
        target_type="webhook",
        target_id=webhook.id,
        meta={"url": webhook.url, "events": list(webhook.events), "enabled": webhook.enabled},
    )
    return webhook


async def delete_webhook(
    session: AsyncSession, workspace_id: str, webhook_id: str, *, actor: Actor
) -> None:
    webhook = await get_webhook(session, workspace_id, webhook_id)
    await session.delete(webhook)
    await session.flush()
    _invalidate_cache(workspace_id)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="webhook.delete",
        target_type="webhook",
        target_id=webhook_id,
    )


async def list_deliveries(
    session: AsyncSession,
    workspace_id: str,
    webhook_id: str,
    *,
    limit: int,
    offset: int,
) -> tuple[list[WebhookDelivery], int]:
    await get_webhook(session, workspace_id, webhook_id)  # 404 if cross-workspace
    base = select(WebhookDelivery).where(
        WebhookDelivery.workspace_id == workspace_id,
        WebhookDelivery.webhook_id == webhook_id,
    )
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await session.execute(
            base.order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return list(rows), total


# ---------------------------------------------------------------------------
# delivery creation + fan-out
# ---------------------------------------------------------------------------


async def enqueue_delivery(
    session: AsyncSession,
    webhook: Webhook,
    *,
    workspace_id: str,
    event_name: str,
    event_payload: dict[str, Any],
) -> WebhookDelivery:
    """Create a pending delivery row (with the exact body to post) and enqueue it."""
    delivery = WebhookDelivery(
        workspace_id=workspace_id,
        webhook_id=webhook.id,
        event_name=event_name,
        payload=_body(event_name, workspace_id, event_payload),
        status="pending",
    )
    session.add(delivery)
    await session.flush()
    await enqueue("deliver_webhook", delivery_id=delivery.id)
    return delivery


# Per-workspace "has ≥1 enabled webhook" cache. Lets the "*" event subscriber skip
# fan-out with zero DB work for the overwhelmingly common no-webhooks case (and
# avoids perturbing unrelated in-process background work on the shared test DB).
# Self-seeds on a workspace's first event and is invalidated on any webhook write,
# so it never yields a false negative (missed delivery) within a process.
_has_enabled_webhooks: dict[str, bool] = {}


def known_no_webhooks(workspace_id: str) -> bool:
    """True only when we've confirmed this workspace has no enabled webhooks."""
    return _has_enabled_webhooks.get(workspace_id) is False


def _invalidate_cache(workspace_id: str) -> None:
    _has_enabled_webhooks.pop(workspace_id, None)


async def fan_out(session: AsyncSession, event: Event) -> None:
    """Deliver a domain event to every enabled webhook that subscribes to it."""
    webhooks = (
        (
            await session.execute(
                select(Webhook).where(
                    Webhook.workspace_id == event.workspace_id,
                    Webhook.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    _has_enabled_webhooks[event.workspace_id] = bool(webhooks)
    for webhook in webhooks:
        subscribed = webhook.events or []
        if "*" in subscribed or event.name in subscribed:
            await enqueue_delivery(
                session,
                webhook,
                workspace_id=event.workspace_id,
                event_name=event.name,
                event_payload=event.payload,
            )


async def send_test(session: AsyncSession, workspace_id: str, webhook_id: str) -> WebhookDelivery:
    """Enqueue a sample delivery so the owner can verify their endpoint."""
    webhook = await get_webhook(session, workspace_id, webhook_id)
    return await enqueue_delivery(
        session,
        webhook,
        workspace_id=workspace_id,
        event_name="webhook.test",
        event_payload={"message": "This is a test event from Stept."},
    )


# ---------------------------------------------------------------------------
# delivery task
# ---------------------------------------------------------------------------


async def _post(
    url: str, body: bytes, headers: dict[str, str]
) -> tuple[bool, int | None, str | None]:
    """Returns (success, response_code, error). Success is a 2xx response."""
    # Re-checked at delivery time, not just at save time: a subscriber's DNS can
    # start pointing at our own network long after the webhook was created.
    try:
        assert_public_url(url)
    except UnsafeUrlError as exc:
        return False, None, f"blocked: {exc}"
    try:
        async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_SECONDS) as client:
            response = await client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        return False, None, f"{exc.__class__.__name__}: {exc}"
    if 200 <= response.status_code < 300:
        return True, response.status_code, None
    return False, response.status_code, f"HTTP {response.status_code}"


@task("deliver_webhook")
async def deliver_webhook(ctx: TaskContext, *, delivery_id: str) -> None:
    """POST one webhook delivery; raise on failure so the queue retries."""
    # Load the delivery + signing material (short transaction, no HTTP inside).
    async with session_scope() as session:
        delivery = await session.get(WebhookDelivery, delivery_id)
        if delivery is None:
            # The enqueuing request may not have committed yet — let the queue retry.
            if ctx.attempt < MAX_ATTEMPTS:
                raise RuntimeError(f"delivery {delivery_id} not visible yet")
            return
        if delivery.status == "success":
            return
        webhook = await session.get(Webhook, delivery.webhook_id)
        if webhook is None:
            delivery.status = "failed"
            delivery.error = "webhook deleted"
            delivery.attempts = ctx.attempt
            return
        body = _encode_body(delivery.payload)
        signature = sign_payload(webhook.secret, body)
        url = webhook.url
        event_name = delivery.event_name

    headers = {
        "Content-Type": "application/json",
        "X-Stept-Event": event_name,
        "X-Stept-Signature": signature,
    }
    success, code, error = await _post(url, body, headers)

    # Persist the outcome in a fresh transaction (committed before any re-raise).
    async with session_scope() as session:
        delivery = await session.get(WebhookDelivery, delivery_id)
        if delivery is None:  # pragma: no cover — deleted mid-flight
            return
        delivery.attempts = ctx.attempt
        delivery.response_code = code
        if success:
            delivery.status = "success"
            delivery.error = None
            return
        delivery.error = (error or "delivery failed")[:1000]
        delivery.status = "pending" if ctx.attempt < MAX_ATTEMPTS else "failed"

    if not success and ctx.attempt < MAX_ATTEMPTS:
        raise RuntimeError(error or "webhook delivery failed")
