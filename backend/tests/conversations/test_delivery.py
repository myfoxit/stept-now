"""Outbound delivery: SENDERS registry, deliver_message task, enqueue wiring."""

from __future__ import annotations

import pytest

from app.channels import registry
from app.core.queue import run_task
from app.models.message import Message
from app.services import conversations as convs
from tests.conversations.conftest import SYSTEM, SvcCtx, make_inbox


@pytest.fixture
def clean_senders():
    """SENDERS is module state — keep tests hermetic."""
    saved = dict(registry.SENDERS)
    registry.SENDERS.clear()
    yield registry.SENDERS
    registry.SENDERS.clear()
    registry.SENDERS.update(saved)


async def _outbound_email_message(svc: SvcCtx, *, deliver: bool) -> Message:
    email_inbox = await make_inbox(svc.session, svc.workspace, channel_type="email")
    conversation = await convs.create_conversation(
        svc.session, inbox=email_inbox, contact=svc.contact, actor=SYSTEM
    )
    return await convs.add_message(
        svc.session,
        conversation,
        direction="out",
        author_type="user",
        author_id=svc.user.id,
        author_name=svc.user.name,
        content="Here is your answer",
        actor=SYSTEM,
        deliver=deliver,
    )


async def test_outbound_on_channel_inbox_enqueues_delivery(svc: SvcCtx, monkeypatch):
    enqueued: list[tuple[str, dict]] = []

    async def record(name: str, **kwargs) -> None:
        enqueued.append((name, kwargs))

    monkeypatch.setattr(convs, "enqueue", record)
    message = await _outbound_email_message(svc, deliver=True)
    assert message.delivery_status == "pending"
    assert enqueued == [("deliver_message", {"message_id": message.id})]


async def test_no_delivery_for_notes_widget_and_inbound(svc: SvcCtx, monkeypatch):
    enqueued: list[str] = []

    async def record(name: str, **kwargs) -> None:
        enqueued.append(name)

    monkeypatch.setattr(convs, "enqueue", record)

    # widget/api channels deliver in-app: sent immediately, nothing enqueued.
    conversation = await convs.create_conversation(
        svc.session, inbox=svc.inbox, contact=svc.contact, actor=SYSTEM
    )
    widget_message = await convs.add_message(
        svc.session,
        conversation,
        direction="out",
        author_type="user",
        author_id=svc.user.id,
        author_name=svc.user.name,
        content="hello",
        actor=SYSTEM,
    )
    assert widget_message.delivery_status == "sent"

    note = await convs.add_message(
        svc.session,
        conversation,
        direction="out",
        author_type="user",
        author_id=svc.user.id,
        author_name=svc.user.name,
        content="internal",
        visibility="note",
        actor=SYSTEM,
    )
    assert note.delivery_status is None

    inbound = await convs.add_message(
        svc.session,
        conversation,
        direction="in",
        author_type="contact",
        author_id=svc.contact.id,
        author_name=svc.contact.name,
        content="from customer",
        actor=SYSTEM,
    )
    assert inbound.delivery_status is None
    assert enqueued == []


async def test_deliver_task_marks_failed_without_sender(svc: SvcCtx, clean_senders):
    message = await _outbound_email_message(svc, deliver=False)
    message.delivery_status = "pending"
    await svc.session.commit()  # task runs in its own session

    await run_task("deliver_message", {"message_id": message.id})

    refreshed = await svc.session.get(Message, message.id)
    await svc.session.refresh(refreshed)
    assert refreshed.delivery_status == "failed"
    assert refreshed.delivery_error == "no sender registered"


async def test_registered_sender_delivers(svc: SvcCtx, clean_senders):
    sent: list[str] = []

    @registry.register_sender("email")
    async def fake_email_sender(session, inbox, message) -> None:
        sent.append(message.id)

    assert registry.SENDERS["email"] is fake_email_sender

    message = await _outbound_email_message(svc, deliver=False)
    message.delivery_status = "pending"
    await svc.session.commit()

    await run_task("deliver_message", {"message_id": message.id})

    refreshed = await svc.session.get(Message, message.id)
    await svc.session.refresh(refreshed)
    assert sent == [message.id]
    assert refreshed.delivery_status == "sent"
    assert refreshed.delivery_error is None


async def test_sender_exception_marks_failed_with_error(svc: SvcCtx, clean_senders):
    @registry.register_sender("email")
    async def broken_sender(session, inbox, message) -> None:
        raise RuntimeError("SMTP connection refused")

    message = await _outbound_email_message(svc, deliver=False)
    message.delivery_status = "pending"
    await svc.session.commit()

    await run_task("deliver_message", {"message_id": message.id})

    refreshed = await svc.session.get(Message, message.id)
    await svc.session.refresh(refreshed)
    assert refreshed.delivery_status == "failed"
    assert "SMTP connection refused" in refreshed.delivery_error
