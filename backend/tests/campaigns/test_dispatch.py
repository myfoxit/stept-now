"""One-off dispatch: audience resolution, per-channel send paths, idempotency."""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import get_session_factory
from app.core.events import EventNames, on
from app.models.conversation import Conversation
from app.models.message import Message
from app.services import campaigns as campaigns_service
from tests.campaigns.conftest import (
    create_campaign_via_api,
    create_contact_inbox_via_db,
    create_contact_via_db,
    create_inbox_via_api,
    create_inbox_via_db,
    create_segment_via_db,
)
from tests.conftest import WorkspaceCtx, drain_tasks


async def _activate(client, ctx, campaign_id: str) -> None:
    response = await client.post(
        f"{ctx.base}/campaigns/{campaign_id}/activate", headers=ctx.owner_headers
    )
    assert response.status_code == 200, response.text


async def test_email_dispatch_to_segment_audience(client, workspace_ctx: WorkspaceCtx, monkeypatch):
    ctx = workspace_ctx
    inbox = await create_inbox_via_api(client, ctx, channel_type="email")
    await create_contact_via_db(
        ctx.id, name="Alice", email="alice@corp.com", attributes={"plan": "pro"}
    )
    await create_contact_via_db(
        ctx.id, name="Bob", email="bob@corp.com", attributes={"plan": "pro"}
    )
    await create_contact_via_db(ctx.id, name="Dave", email=None, attributes={"plan": "pro"})
    await create_contact_via_db(
        ctx.id, name="Carol", email="carol@other.com", attributes={"plan": "free"}
    )
    segment_id = await create_segment_via_db(
        ctx.id, filters=[{"field": "attributes.plan", "op": "eq", "value": "pro"}]
    )
    campaign = await create_campaign_via_api(
        client,
        ctx,
        inbox_id=inbox["id"],
        audience={"type": "segment", "segment_id": segment_id},
    )
    await _activate(client, ctx, campaign["id"])

    sent: list[dict] = []

    async def fake_send_email(to, subject, html, **kwargs):
        sent.append({"to": to, "subject": subject, "html": html})
        return True

    monkeypatch.setattr("app.services.email.send_email", fake_send_email)

    captured_events: list[dict] = []

    async def _capture(session, event):
        captured_events.append(dict(event.payload))

    on(EventNames.CAMPAIGN_SENT)(_capture)

    assert await campaigns_service.dispatch_due_campaigns() == 1

    assert {call["to"] for call in sent} == {"alice@corp.com", "bob@corp.com"}
    assert all(call["subject"] == "Spring launch" for call in sent)
    by_to = {call["to"]: call["html"] for call in sent}
    assert by_to["alice@corp.com"] == "Hello Alice!"  # {{contact.name}} rendered
    assert by_to["bob@corp.com"] == "Hello Bob!"

    detail = (
        await client.get(f"{ctx.base}/campaigns/{campaign['id']}", headers=ctx.owner_headers)
    ).json()
    assert detail["status"] == "completed"
    assert detail["sent_count"] == 2  # Dave (no email) skipped, Carol not in segment

    assert any(
        p.get("campaign_id") == campaign["id"] and p.get("sent_count") == 2 for p in captured_events
    )

    # Second run: the campaign is completed — nothing is re-sent.
    assert await campaigns_service.dispatch_due_campaigns() == 0
    assert len(sent) == 2


async def test_sms_dispatch_only_contacts_with_channel_identity(
    client, workspace_ctx: WorkspaceCtx, monkeypatch
):
    ctx = workspace_ctx
    inbox_id = await create_inbox_via_db(ctx.id, channel_type="sms")
    ann_id = await create_contact_via_db(ctx.id, name="Ann")
    await create_contact_via_db(ctx.id, name="Ben")  # no ContactInbox → skipped
    await create_contact_inbox_via_db(ctx.id, ann_id, inbox_id, source_id="+15550001111")

    campaign = await create_campaign_via_api(
        client, ctx, inbox_id=inbox_id, message="Hi {{contact.name}}, big news!"
    )
    await _activate(client, ctx, campaign["id"])

    delivered: list[str] = []

    async def fake_sender(session, inbox_model, message):
        delivered.append(message.id)

    from app.channels.registry import SENDERS

    monkeypatch.setitem(SENDERS, "sms", fake_sender)

    assert await campaigns_service.dispatch_due_campaigns() == 1
    await drain_tasks()  # channel delivery runs on the task queue

    async with get_session_factory()() as session:
        conversations = list(
            (
                await session.execute(
                    select(Conversation).where(Conversation.workspace_id == ctx.id)
                )
            ).scalars()
        )
        assert len(conversations) == 1  # Ann only — no implicit opt-in for Ben
        conversation = conversations[0]
        assert conversation.contact_id == ann_id
        assert conversation.attributes == {"campaign_id": campaign["id"]}

        message = (
            await session.execute(select(Message).where(Message.conversation_id == conversation.id))
        ).scalar_one()
        assert message.direction == "out"
        assert message.content == "Hi Ann, big news!"
        assert message.author_name == "Campaign"  # no sender member configured
        assert message.meta == {"campaign_id": campaign["id"]}
        assert message.delivery_status == "sent"

    assert delivered == [message.id]
    detail = (
        await client.get(f"{ctx.base}/campaigns/{campaign['id']}", headers=ctx.owner_headers)
    ).json()
    assert detail["sent_count"] == 1
    assert detail["status"] == "completed"
