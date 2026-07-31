"""Engine integration tests: rules fire on emitted events and run their actions."""

from __future__ import annotations

from sqlalchemy import select

from app.core.events import Actor, Event, EventNames, emit
from app.models.message import Message
from app.models.notification import Notification
from app.services import conversations as conversations_service
from tests.automation.conftest import (
    AutoCtx,
    insert_conversation,
    make_contact,
    make_rule,
)

SYSTEM = Actor.system()


async def _automation_replies(session, conversation_id) -> list[Message]:
    rows = await session.execute(
        select(Message).where(
            Message.conversation_id == conversation_id, Message.author_name == "Automation"
        )
    )
    return list(rows.scalars())


async def test_conversation_created_fires_matching_rule(auto: AutoCtx):
    session, ws, inbox = auto.session, auto.workspace, auto.inbox
    await make_rule(
        session,
        ws,
        event=EventNames.CONVERSATION_CREATED,
        conditions=[{"field": "contact.attributes.plan", "op": "eq", "value": "enterprise"}],
        actions=[
            {"type": "add_tag", "params": {"tag": "vip"}},
            {"type": "set_priority", "params": {"priority": "high"}},
        ],
    )
    vip = await make_contact(session, ws, name="Enterprise Ed", attributes={"plan": "enterprise"})

    conversation = await conversations_service.create_conversation(
        session, inbox=inbox, contact=vip, actor=SYSTEM
    )

    assert conversation.priority == "high"
    tag_ids = await conversations_service.tag_ids_for(session, conversation.id)
    assert len(tag_ids) == 1


async def test_rule_skipped_when_condition_not_met(auto: AutoCtx):
    session, ws, inbox = auto.session, auto.workspace, auto.inbox
    await make_rule(
        session,
        ws,
        event=EventNames.CONVERSATION_CREATED,
        conditions=[{"field": "contact.attributes.plan", "op": "eq", "value": "enterprise"}],
        actions=[{"type": "set_priority", "params": {"priority": "high"}}],
    )
    free = await make_contact(session, ws, name="Free Fran", attributes={"plan": "free"})

    conversation = await conversations_service.create_conversation(
        session, inbox=inbox, contact=free, actor=SYSTEM
    )

    assert conversation.priority == "none"
    assert await conversations_service.tag_ids_for(session, conversation.id) == []


async def test_message_created_send_reply_and_loop_prevention(auto: AutoCtx):
    session, ws, inbox, contact = auto.session, auto.workspace, auto.inbox, auto.contact
    await make_rule(
        session,
        ws,
        event=EventNames.MESSAGE_CREATED,
        actions=[{"type": "send_reply", "params": {"content": "Thanks, we are on it!"}}],
    )
    conversation = await insert_conversation(session, ws, inbox, contact)

    inbound = await conversations_service.add_message(
        session,
        conversation,
        direction="in",
        author_type="contact",
        author_id=contact.id,
        author_name="Nina",
        content="Hello?",
        actor=Actor(type="contact", id=contact.id),
    )

    # Exactly one automation reply — the system-authored reply did not re-trigger.
    replies = await _automation_replies(session, conversation.id)
    assert len(replies) == 1
    assert replies[0].content == "Thanks, we are on it!"

    # Re-emitting the same message.created event must not act twice (per-message cap).
    await emit(
        session,
        Event(
            name=EventNames.MESSAGE_CREATED,
            workspace_id=ws.id,
            payload={
                "conversation_id": conversation.id,
                "message_id": inbound.id,
                "direction": "in",
                "author_type": "contact",
            },
            actor=SYSTEM,
        ),
    )
    assert len(await _automation_replies(session, conversation.id)) == 1


async def test_message_created_skips_system_authored(auto: AutoCtx):
    session, ws, inbox, contact = auto.session, auto.workspace, auto.inbox, auto.contact
    await make_rule(
        session,
        ws,
        event=EventNames.MESSAGE_CREATED,
        actions=[{"type": "send_reply", "params": {"content": "auto"}}],
    )
    conversation = await insert_conversation(session, ws, inbox, contact)

    await emit(
        session,
        Event(
            name=EventNames.MESSAGE_CREATED,
            workspace_id=ws.id,
            payload={
                "conversation_id": conversation.id,
                "message_id": "does-not-matter",
                "author_type": "system",
            },
            actor=SYSTEM,
        ),
    )
    assert await _automation_replies(session, conversation.id) == []


async def test_rule_ordering_last_wins(auto: AutoCtx):
    session, ws, inbox, contact = auto.session, auto.workspace, auto.inbox, auto.contact
    await make_rule(
        session,
        ws,
        event=EventNames.CONVERSATION_CREATED,
        actions=[{"type": "set_priority", "params": {"priority": "low"}}],
        ord=0,
        name="first",
    )
    await make_rule(
        session,
        ws,
        event=EventNames.CONVERSATION_CREATED,
        actions=[{"type": "set_priority", "params": {"priority": "urgent"}}],
        ord=1,
        name="second",
    )

    conversation = await conversations_service.create_conversation(
        session, inbox=inbox, contact=contact, actor=SYSTEM
    )
    assert conversation.priority == "urgent"


async def test_disabled_rule_does_not_fire(auto: AutoCtx):
    session, ws, inbox, contact = auto.session, auto.workspace, auto.inbox, auto.contact
    await make_rule(
        session,
        ws,
        event=EventNames.CONVERSATION_CREATED,
        actions=[{"type": "set_priority", "params": {"priority": "high"}}],
        enabled=False,
    )
    conversation = await conversations_service.create_conversation(
        session, inbox=inbox, contact=contact, actor=SYSTEM
    )
    assert conversation.priority == "none"


async def test_notify_member_action(auto: AutoCtx):
    session, ws, inbox, contact, user = (
        auto.session,
        auto.workspace,
        auto.inbox,
        auto.contact,
        auto.user,
    )
    await make_rule(
        session,
        ws,
        event=EventNames.CONVERSATION_CREATED,
        actions=[
            {"type": "notify_member", "params": {"user_id": user.id, "title": "New conversation"}}
        ],
    )
    await conversations_service.create_conversation(
        session, inbox=inbox, contact=contact, actor=SYSTEM
    )

    notes = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.workspace_id == ws.id, Notification.user_id == user.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notes) == 1
    assert notes[0].title == "New conversation"


async def test_set_status_action(auto: AutoCtx):
    session, ws, inbox, contact = auto.session, auto.workspace, auto.inbox, auto.contact
    await make_rule(
        session,
        ws,
        event=EventNames.CONVERSATION_CREATED,
        actions=[{"type": "set_status", "params": {"status": "pending"}}],
    )
    conversation = await conversations_service.create_conversation(
        session, inbox=inbox, contact=contact, actor=SYSTEM
    )
    assert conversation.status == "pending"


async def test_contact_created_event_fires(auto: AutoCtx):
    session, ws, user = auto.session, auto.workspace, auto.user
    await make_rule(
        session,
        ws,
        event=EventNames.CONTACT_CREATED,
        actions=[{"type": "notify_member", "params": {"user_id": user.id, "title": "New contact"}}],
    )
    new_contact = await make_contact(session, ws, name="Fresh")

    await emit(
        session,
        Event(
            name=EventNames.CONTACT_CREATED,
            workspace_id=ws.id,
            payload={"contact_id": new_contact.id},
            actor=SYSTEM,
        ),
    )
    notes = (
        (await session.execute(select(Notification).where(Notification.user_id == user.id)))
        .scalars()
        .all()
    )
    assert len(notes) == 1
