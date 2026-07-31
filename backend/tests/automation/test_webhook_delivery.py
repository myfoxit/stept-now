"""Webhook delivery task + fan-out: signing, retries, and event selection.

The delivery task is driven with `run_task` (deterministic, single-threaded) so
tests never depend on background scheduling on the shared SQLite connection.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.events import Event, EventNames
from app.core.queue import run_task
from app.models.webhook import WebhookDelivery
from app.services import webhooks as webhooks_service
from app.services.webhooks import sign_payload
from tests.automation.conftest import (
    AutoCtx,
    make_delivery,
    make_webhook,
)


def test_sign_payload_matches_hmac():
    body = b'{"event":"x"}'
    sig = sign_payload("secret", body)
    import hashlib
    import hmac

    assert sig == hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert sign_payload("secret", body) != sign_payload("other", body)


async def test_delivery_success_is_signed(auto: AutoCtx):
    session, ws = auto.session, auto.workspace
    webhook = await make_webhook(session, ws, url="https://hooks.example.com/ok", secret="sk_1")
    delivery = await make_delivery(session, ws, webhook, event_name="conversation.created")
    await session.commit()

    with respx.mock:
        route = respx.post("https://hooks.example.com/ok").mock(return_value=httpx.Response(200))
        await run_task("deliver_webhook", {"delivery_id": delivery.id})

    assert route.called
    request = route.calls.last.request
    assert request.headers["X-Stept-Event"] == "conversation.created"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["X-Stept-Signature"] == sign_payload("sk_1", request.content)

    refreshed = await session.get(WebhookDelivery, delivery.id)
    await session.refresh(refreshed)
    assert refreshed.status == "success"
    assert refreshed.response_code == 200
    assert refreshed.attempts == 1
    assert refreshed.error is None


async def test_delivery_retries_then_marks_failed(auto: AutoCtx):
    session, ws = auto.session, auto.workspace
    webhook = await make_webhook(session, ws, url="https://hooks.example.com/500")
    delivery = await make_delivery(session, ws, webhook)
    await session.commit()

    with respx.mock:
        route = respx.post("https://hooks.example.com/500").mock(return_value=httpx.Response(500))
        # Non-final attempts re-raise so the queue would retry.
        for attempt in (1, 2):
            with pytest.raises(RuntimeError):
                await run_task("deliver_webhook", {"delivery_id": delivery.id}, attempt=attempt)
        # Final attempt records failure without raising.
        await run_task("deliver_webhook", {"delivery_id": delivery.id}, attempt=3)

    assert route.call_count == 3
    refreshed = await session.get(WebhookDelivery, delivery.id)
    await session.refresh(refreshed)
    assert refreshed.status == "failed"
    assert refreshed.response_code == 500
    assert refreshed.attempts == 3


async def test_delivery_transport_error_is_recorded(auto: AutoCtx):
    session, ws = auto.session, auto.workspace
    webhook = await make_webhook(session, ws, url="https://hooks.example.com/down")
    delivery = await make_delivery(session, ws, webhook)
    await session.commit()

    with respx.mock:
        respx.post("https://hooks.example.com/down").mock(side_effect=httpx.ConnectError("refused"))
        await run_task("deliver_webhook", {"delivery_id": delivery.id}, attempt=3)

    refreshed = await session.get(WebhookDelivery, delivery.id)
    await session.refresh(refreshed)
    assert refreshed.status == "failed"
    assert refreshed.error and "ConnectError" in refreshed.error


async def test_delivery_missing_row_retries_then_gives_up(auto: AutoCtx):
    # Not visible yet on early attempts → raise so the queue retries.
    with pytest.raises(RuntimeError):
        await run_task("deliver_webhook", {"delivery_id": "nonexistent"}, attempt=1)
    # Final attempt: give up quietly (no raise).
    await run_task("deliver_webhook", {"delivery_id": "nonexistent"}, attempt=3)


async def test_fan_out_selects_enabled_matching_webhooks(auto: AutoCtx, monkeypatch):
    session, ws = auto.session, auto.workspace
    enqueued: list[str] = []

    async def _record(name: str, **kwargs) -> None:
        enqueued.append(kwargs["delivery_id"])

    monkeypatch.setattr(webhooks_service, "enqueue", _record)

    matching = await make_webhook(
        session, ws, url="https://h/a", events=[EventNames.CONVERSATION_CREATED]
    )
    wildcard = await make_webhook(session, ws, url="https://h/b", events=["*"])
    non_matching = await make_webhook(
        session, ws, url="https://h/c", events=[EventNames.CSAT_SUBMITTED]
    )
    disabled = await make_webhook(session, ws, url="https://h/d", events=["*"], enabled=False)

    await webhooks_service.fan_out(
        session,
        Event(
            name=EventNames.CONVERSATION_CREATED,
            workspace_id=ws.id,
            payload={"conversation_id": "conv-1"},
        ),
    )

    deliveries = (await session.execute(WebhookDelivery.__table__.select())).fetchall()
    delivered_webhook_ids = {row.webhook_id for row in deliveries}
    assert matching.id in delivered_webhook_ids
    assert wildcard.id in delivered_webhook_ids
    assert non_matching.id not in delivered_webhook_ids
    assert disabled.id not in delivered_webhook_ids
    assert len(enqueued) == 2  # one per matching enabled webhook


async def test_known_no_webhooks_cache(auto: AutoCtx):
    session, ws = auto.session, auto.workspace
    # Fresh workspace: unknown → not treated as confirmed-empty (won't skip).
    assert webhooks_service.known_no_webhooks(ws.id) is False

    await webhooks_service.fan_out(
        session, Event(name=EventNames.CONVERSATION_CREATED, workspace_id=ws.id, payload={})
    )
    # No webhooks → cache now confirms empty (enables the fast-skip path).
    assert webhooks_service.known_no_webhooks(ws.id) is True

    await make_webhook(session, ws, url="https://h/z", events=["*"])
    await webhooks_service.fan_out(
        session, Event(name=EventNames.CONVERSATION_CREATED, workspace_id=ws.id, payload={})
    )
    assert webhooks_service.known_no_webhooks(ws.id) is False  # now it has one
