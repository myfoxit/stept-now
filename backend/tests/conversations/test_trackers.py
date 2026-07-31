"""ingest_inbound pipeline + the tracker-column matrix (waiting_since /
first_reply_at / last_activity_at / unread) — the Chatwoot semantics."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import ContactInbox
from app.models.message import Message
from app.services import conversations as convs
from tests.conversations.conftest import SYSTEM, SvcCtx, make_contact, make_workspace


async def _inbound(svc: SvcCtx, conversation, content="ping", **kwargs):
    return await convs.add_message(
        svc.session,
        conversation,
        direction="in",
        author_type="contact",
        author_id=svc.contact.id,
        author_name=svc.contact.name,
        content=content,
        actor=SYSTEM,
        **kwargs,
    )


async def _reply(svc: SvcCtx, conversation, content="pong", author_type="user", **kwargs):
    return await convs.add_message(
        svc.session,
        conversation,
        direction="out",
        author_type=author_type,
        author_id=svc.user.id,
        author_name=svc.user.name,
        content=content,
        actor=SYSTEM,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# ingest_inbound
# ---------------------------------------------------------------------------


async def test_ingest_creates_contact_identity_conversation_and_message(svc: SvcCtx):
    conversation, message = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="visitor-1",
        content="Hello, I need help",
        contact_info={"name": "Maya Chen", "email": "maya@acme.io"},
    )
    assert conversation.number == 1
    assert conversation.status == "open"
    assert conversation.waiting_since is not None
    assert message.direction == "in"
    assert message.visibility == "public"
    assert message.author_type == "contact"
    assert message.delivery_status is None

    contact = await svc.session.get(Contact, conversation.contact_id)
    assert contact is not None and contact.email == "maya@acme.io"
    contact_inbox = await svc.session.get(ContactInbox, conversation.contact_inbox_id)
    assert contact_inbox is not None
    assert contact_inbox.source_id == "visitor-1"
    assert contact_inbox.inbox_id == svc.inbox.id


async def test_ingest_reuses_open_conversation_and_contact(svc: SvcCtx):
    first, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="visitor-2",
        content="First message",
        contact_info={"email": "repeat@example.com"},
    )
    second, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="visitor-2",
        content="Second message",
        contact_info={"email": "repeat@example.com"},
    )
    assert second.id == first.id
    message_count = (
        await svc.session.execute(
            select(func.count()).select_from(Message).where(Message.conversation_id == first.id)
        )
    ).scalar_one()
    assert message_count == 2
    contact_count = (
        await svc.session.execute(
            select(func.count())
            .select_from(Contact)
            .where(Contact.workspace_id == svc.workspace.id, Contact.email == "repeat@example.com")
        )
    ).scalar_one()
    assert contact_count == 1


async def test_ingest_dedupes_by_message_source_id(svc: SvcCtx):
    conversation, message = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="visitor-3",
        content="delivered once",
        contact_info={"email": "dedupe@example.com"},
        message_source_id="email-msg-123",
    )
    again, redelivered = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="visitor-3",
        content="delivered once (retry)",
        contact_info={"email": "dedupe@example.com"},
        message_source_id="email-msg-123",
    )
    assert again.id == conversation.id
    assert redelivered.id == message.id
    total = (
        await svc.session.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation.id)
        )
    ).scalar_one()
    assert total == 1


async def test_ingest_creates_new_conversation_after_resolve(svc: SvcCtx):
    first, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="visitor-4",
        content="Original issue",
        contact_info={"email": "back@example.com"},
    )
    await convs.update_status(svc.session, first, "resolved", actor=SYSTEM)
    second, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="visitor-4",
        content="New issue, new thread",
        contact_info={"email": "back@example.com"},
    )
    assert second.id != first.id
    assert second.number == first.number + 1
    assert second.contact_id == first.contact_id  # same identity spine


# ---------------------------------------------------------------------------
# reopen semantics
# ---------------------------------------------------------------------------


async def test_inbound_reopens_resolved_conversation(svc: SvcCtx):
    conversation, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="v-reopen",
        content="hi",
        contact_info={"email": "r@example.com"},
    )
    await convs.update_status(svc.session, conversation, "resolved", actor=SYSTEM)
    assert conversation.waiting_since is None
    await _inbound(svc, conversation, "are you still there?")
    assert conversation.status == "open"
    assert conversation.waiting_since is not None


async def test_inbound_keeps_pending_conversation_pending(svc: SvcCtx):
    conversation, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="v-pending",
        content="hi",
        contact_info={"email": "p@example.com"},
    )
    await convs.update_status(svc.session, conversation, "pending", actor=SYSTEM)
    await _inbound(svc, conversation, "another question")
    assert conversation.status == "pending"  # AI agent keeps ownership


async def test_inbound_reopens_snoozed_and_clears_snoozed_until(svc: SvcCtx):
    from datetime import timedelta

    from app.core.db import utcnow

    conversation, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="v-snooze",
        content="hi",
        contact_info={"email": "s@example.com"},
    )
    await convs.update_status(
        svc.session,
        conversation,
        "snoozed",
        actor=SYSTEM,
        snoozed_until=utcnow() + timedelta(days=1),
    )
    assert conversation.snoozed_until is not None
    await _inbound(svc, conversation, "customer is back")
    assert conversation.status == "open"
    assert conversation.snoozed_until is None


# ---------------------------------------------------------------------------
# waiting_since / first_reply_at
# ---------------------------------------------------------------------------


async def test_waiting_since_set_once_and_cleared_by_reply(svc: SvcCtx):
    conversation, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="v-wait",
        content="first",
        contact_info={"email": "w@example.com"},
    )
    started_waiting = conversation.waiting_since
    assert started_waiting is not None
    await _inbound(svc, conversation, "second inbound")
    assert conversation.waiting_since == started_waiting  # not moved by more inbound

    await _reply(svc, conversation, "on it!")
    assert conversation.waiting_since is None  # human reply clears the queue

    await _inbound(svc, conversation, "thanks, one more thing")
    assert conversation.waiting_since is not None
    assert conversation.waiting_since > started_waiting


async def test_first_reply_at_set_once_by_user_reply(svc: SvcCtx):
    conversation, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="v-frt",
        content="hello?",
        contact_info={"email": "f@example.com"},
    )
    assert conversation.first_reply_at is None
    await _reply(svc, conversation, "first human reply")
    first_reply_at = conversation.first_reply_at
    assert first_reply_at is not None
    await _reply(svc, conversation, "second reply")
    assert conversation.first_reply_at == first_reply_at  # FRT is immutable


async def test_ai_agent_reply_counts_for_first_reply_and_waiting(svc: SvcCtx):
    conversation, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="v-ai",
        content="question",
        contact_info={"email": "ai@example.com"},
    )
    await _reply(svc, conversation, "AI answer", author_type="agent")
    assert conversation.first_reply_at is not None
    assert conversation.waiting_since is None


async def test_notes_only_bump_last_activity(svc: SvcCtx):
    conversation, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="v-note",
        content="hello",
        contact_info={"email": "n@example.com"},
    )
    waiting_before = conversation.waiting_since
    activity_before = conversation.last_activity_at
    await _reply(svc, conversation, "internal note", visibility="note")
    assert conversation.waiting_since == waiting_before  # unchanged
    assert conversation.first_reply_at is None  # notes are not replies
    assert conversation.last_activity_at > activity_before


# ---------------------------------------------------------------------------
# unread (timestamps, not counters)
# ---------------------------------------------------------------------------


async def test_unread_count_and_mark_read(svc: SvcCtx):
    conversation, _ = await convs.ingest_inbound(
        svc.session,
        svc.inbox,
        source_id="v-unread",
        content="one",
        contact_info={"email": "u@example.com"},
    )
    await _inbound(svc, conversation, "two")
    await _reply(svc, conversation, "outbound does not count")
    assert await convs.unread_count(svc.session, conversation) == 2

    await convs.mark_read(svc.session, conversation)
    assert await convs.unread_count(svc.session, conversation) == 0

    await _inbound(svc, conversation, "three")
    assert await convs.unread_count(svc.session, conversation) == 1


# ---------------------------------------------------------------------------
# numbering
# ---------------------------------------------------------------------------


async def test_number_sequence_is_per_workspace(svc: SvcCtx):
    for expected in (1, 2, 3):
        conversation = await convs.create_conversation(
            svc.session,
            inbox=svc.inbox,
            contact=svc.contact,
            actor=SYSTEM,
        )
        assert conversation.number == expected

    other_ws = await make_workspace(svc.session, "Other WS")
    from tests.conversations.conftest import make_inbox

    other_inbox = await make_inbox(svc.session, other_ws)
    other_contact = await make_contact(svc.session, other_ws)
    other = await convs.create_conversation(
        svc.session, inbox=other_inbox, contact=other_contact, actor=SYSTEM
    )
    assert other.number == 1  # sequences never cross workspaces

    numbers = (
        (
            await svc.session.execute(
                select(Conversation.number).where(Conversation.workspace_id == svc.workspace.id)
            )
        )
        .scalars()
        .all()
    )
    assert sorted(numbers) == [1, 2, 3]
