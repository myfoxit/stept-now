"""Webhooks API tests: CRUD, validation, authz, deliveries listing, fan-out wiring."""

from __future__ import annotations

from app.core.db import get_session_factory
from app.core.events import EventNames
from app.models.webhook import WebhookDelivery
from app.services import webhooks as webhooks_service
from tests.automation.conftest import create_contact_via_db, create_inbox_via_api
from tests.conftest import bearer, signup


async def _make_webhook(client, ctx, *, url, events, enabled=True) -> dict:
    resp = await client.post(
        f"{ctx.base}/webhooks",
        json={"url": url, "events": events, "enabled": enabled},
        headers=ctx.owner_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_crud_and_secret(client, workspace_ctx):
    base, headers = workspace_ctx.base, workspace_ctx.owner_headers
    created = await _make_webhook(
        client, workspace_ctx, url="https://hooks.example.com/a", events=["*"]
    )
    assert created["secret"]
    assert created["events"] == ["*"]
    webhook_id = created["id"]

    listing = await client.get(f"{base}/webhooks", headers=headers)
    assert [w["id"] for w in listing.json()] == [webhook_id]

    patched = await client.patch(
        f"{base}/webhooks/{webhook_id}",
        json={"enabled": False, "events": [EventNames.CONVERSATION_CREATED]},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["events"] == [EventNames.CONVERSATION_CREATED]

    deleted = await client.delete(f"{base}/webhooks/{webhook_id}", headers=headers)
    assert deleted.status_code == 200
    assert (await client.get(f"{base}/webhooks/{webhook_id}", headers=headers)).status_code == 404


async def test_validation_rejects_bad_url_and_event(client, workspace_ctx):
    base, headers = workspace_ctx.base, workspace_ctx.owner_headers
    bad_url = await client.post(
        f"{base}/webhooks", json={"url": "ftp://x", "events": ["*"]}, headers=headers
    )
    assert bad_url.status_code == 422
    bad_event = await client.post(
        f"{base}/webhooks",
        json={"url": "https://x.example.com", "events": ["nope.nope"]},
        headers=headers,
    )
    assert bad_event.status_code == 422
    empty = await client.post(
        f"{base}/webhooks", json={"url": "https://x.example.com", "events": []}, headers=headers
    )
    assert empty.status_code == 422


async def test_authz(client, workspace_ctx):
    base = workspace_ctx.base
    agent = await workspace_ctx.add_member("agent@example.com", role="agent")
    # agent lacks webhooks:manage
    assert (await client.get(f"{base}/webhooks", headers=agent)).status_code == 403
    denied = await client.post(
        f"{base}/webhooks", json={"url": "https://x.example.com", "events": ["*"]}, headers=agent
    )
    assert denied.status_code == 403

    # cross-workspace principal has no membership → 403
    intruder = await signup(client, "intruder-wh@example.com")
    await client.post("/api/v1/workspaces", json={"name": "Theirs"}, headers=bearer(intruder))
    assert (await client.get(f"{base}/webhooks", headers=bearer(intruder))).status_code == 403


async def test_deliveries_listing_pagination(client, workspace_ctx):
    base, headers = workspace_ctx.base, workspace_ctx.owner_headers
    webhook = await _make_webhook(
        client, workspace_ctx, url="https://hooks.example.com/list", events=["*"]
    )

    async with get_session_factory()() as session:
        for i in range(3):
            session.add(
                WebhookDelivery(
                    workspace_id=workspace_ctx.id,
                    webhook_id=webhook["id"],
                    event_name="conversation.created",
                    payload={"n": i},
                    status="success",
                    response_code=200,
                    attempts=1,
                )
            )
        await session.commit()

    page = await client.get(
        f"{base}/webhooks/{webhook['id']}/deliveries?limit=2&offset=0", headers=headers
    )
    body = page.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["items"][0]["status"] == "success"

    # cross-workspace / unknown webhook → 404
    missing = await client.get(f"{base}/webhooks/nope/deliveries", headers=headers)
    assert missing.status_code == 404


async def test_test_endpoint_creates_pending_delivery(client, workspace_ctx, monkeypatch):
    base, headers = workspace_ctx.base, workspace_ctx.owner_headers
    webhook = await _make_webhook(
        client, workspace_ctx, url="https://hooks.example.com/t", events=["*"]
    )

    async def _noop(name: str, **kwargs) -> None:  # don't schedule a real background task
        return None

    monkeypatch.setattr(webhooks_service, "enqueue", _noop)

    resp = await client.post(f"{base}/webhooks/{webhook['id']}/test", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["event_name"] == "webhook.test"
    assert resp.json()["status"] == "pending"


async def test_fan_out_subscriber_registered_end_to_end(client, workspace_ctx, monkeypatch):
    """Creating a conversation fans out to a matching webhook — proves the '*'
    subscriber registered at app build via the shipped router import."""
    base, headers = workspace_ctx.base, workspace_ctx.owner_headers
    delivered: list[str] = []

    async def _record(name: str, **kwargs) -> None:  # don't run the real HTTP task
        delivered.append(kwargs.get("delivery_id", ""))

    monkeypatch.setattr(webhooks_service, "enqueue", _record)

    webhook = await _make_webhook(
        client,
        workspace_ctx,
        url="https://hooks.example.com/fanout",
        events=[EventNames.CONVERSATION_CREATED],
    )
    inbox = await create_inbox_via_api(client, workspace_ctx, channel_type="widget")
    contact_id = await create_contact_via_db(workspace_ctx.id, name="Fan Out")

    created = await client.post(
        f"{base}/conversations",
        json={"contact_id": contact_id, "inbox_id": inbox["id"], "content": "Hello"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert len(delivered) == 1  # one delivery enqueued for conversation.created

    deliveries = await client.get(f"{base}/webhooks/{webhook['id']}/deliveries", headers=headers)
    body = deliveries.json()
    assert body["total"] == 1
    assert body["items"][0]["event_name"] == EventNames.CONVERSATION_CREATED
    assert body["items"][0]["status"] == "pending"
