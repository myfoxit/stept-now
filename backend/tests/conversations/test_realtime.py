"""Realtime broadcasts: message.created / conversation.created / conversation.updated
on the ws:{workspace} and conv:{conversation} pub/sub topics."""

from __future__ import annotations

import asyncio

from app.core.pubsub import get_pubsub
from app.realtime.manager import conversation_topic, workspace_topic
from app.services import conversations as convs
from tests.conversations.conftest import SYSTEM, SvcCtx, tag_id_in_session


async def _next(subscription, timeout: float = 2.0) -> dict:
    return await asyncio.wait_for(subscription.queue.get(), timeout)


async def test_message_created_broadcast_to_both_topics(svc: SvcCtx):
    conversation = await convs.create_conversation(
        svc.session, inbox=svc.inbox, contact=svc.contact, actor=SYSTEM
    )
    ws_sub = await get_pubsub().subscribe(workspace_topic(svc.workspace.id))
    conv_sub = await get_pubsub().subscribe(conversation_topic(conversation.id))

    message = await convs.add_message(
        svc.session,
        conversation,
        direction="in",
        author_type="contact",
        author_id=svc.contact.id,
        author_name=svc.contact.name,
        content="realtime hello",
        actor=SYSTEM,
    )

    for subscription in (ws_sub, conv_sub):
        envelope = await _next(subscription)
        assert envelope["type"] == "message.created"
        payload = envelope["data"]
        assert payload["message"]["id"] == message.id
        assert payload["message"]["content"] == "realtime hello"
        assert payload["message"]["direction"] == "in"
        assert payload["conversation"]["id"] == conversation.id
        assert payload["conversation"]["status"] == "open"
        assert payload["conversation"]["waiting_since"] is not None
    await ws_sub.close()
    await conv_sub.close()


async def test_conversation_created_broadcast(svc: SvcCtx):
    ws_sub = await get_pubsub().subscribe(workspace_topic(svc.workspace.id))
    conversation = await convs.create_conversation(
        svc.session, inbox=svc.inbox, contact=svc.contact, actor=SYSTEM, subject="New thread"
    )
    envelope = await _next(ws_sub)
    assert envelope["type"] == "conversation.created"
    assert envelope["data"]["id"] == conversation.id
    assert envelope["data"]["subject"] == "New thread"
    assert envelope["data"]["contact"]["id"] == svc.contact.id
    assert envelope["data"]["inbox"]["channel_type"] == "widget"
    await ws_sub.close()


async def test_conversation_updated_broadcast_on_status_priority_tags(svc: SvcCtx):
    conversation = await convs.create_conversation(
        svc.session, inbox=svc.inbox, contact=svc.contact, actor=SYSTEM
    )
    ws_sub = await get_pubsub().subscribe(workspace_topic(svc.workspace.id))

    await convs.set_priority(svc.session, conversation, "urgent", actor=SYSTEM)
    envelope = await _next(ws_sub)
    assert envelope["type"] == "conversation.updated"
    assert envelope["data"]["priority"] == "urgent"

    await convs.update_status(svc.session, conversation, "resolved", actor=SYSTEM)
    # status change emits activity message.created + conversation.updated;
    # drain until the conversation.updated with resolved arrives.
    for _ in range(4):
        envelope = await _next(ws_sub)
        if envelope["type"] == "conversation.updated" and envelope["data"]["status"] == "resolved":
            break
    else:  # pragma: no cover
        raise AssertionError("conversation.updated with resolved status not received")
    assert envelope["data"]["resolved_at"] is not None

    tag_id = await tag_id_in_session(svc.session, svc.workspace.id)
    await convs.add_tag(svc.session, conversation, tag_id, actor=SYSTEM)
    for _ in range(4):
        envelope = await _next(ws_sub)
        if envelope["type"] == "conversation.updated" and envelope["data"]["tag_ids"]:
            break
    else:  # pragma: no cover
        raise AssertionError("conversation.updated with tags not received")
    assert envelope["data"]["tag_ids"] == [tag_id]
    await ws_sub.close()


async def test_assignment_broadcasts_conversation_updated(svc: SvcCtx):
    conversation = await convs.create_conversation(
        svc.session, inbox=svc.inbox, contact=svc.contact, actor=SYSTEM
    )
    conv_sub = await get_pubsub().subscribe(conversation_topic(conversation.id))
    await convs.assign(svc.session, conversation, assignee_user_id=svc.user.id, actor=SYSTEM)
    for _ in range(4):
        envelope = await _next(conv_sub)
        if envelope["type"] == "conversation.updated":
            break
    else:  # pragma: no cover
        raise AssertionError("conversation.updated not received")
    assert envelope["data"]["assignee"] == {"id": svc.user.id, "name": svc.user.name}
    await conv_sub.close()
