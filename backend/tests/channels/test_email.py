"""Email channel: inbound routing + quoted-tail stripping + dedupe, outbound SMTP."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select

from app.channels.email import reply_address, strip_quoted
from app.core.db import get_session_factory
from app.models.conversation import Conversation
from app.models.message import Message, MessageDirection
from tests.channels.conftest import make_inbox, make_outbound_message, make_workspace

ADDRESS = "support@acme.com"


async def _email_inbox() -> tuple[str, str]:
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id, channel_type="email", config={"address": ADDRESS}, name="Email"
    )
    return workspace_id, inbox_id


async def _message_count(conversation_id: str) -> int:
    async with get_session_factory()() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_id)
            )
        ).scalar_one()


# --- pure helpers -----------------------------------------------------------


def test_strip_quoted_on_wrote():
    text = "My actual reply\n\nOn Mon, Jan 1, 2024 at 9:00 AM Sam <s@acme.com> wrote:\n> earlier"
    assert strip_quoted(text) == "My actual reply"


def test_strip_quoted_original_message():
    text = "Thanks!\n-----Original Message-----\nFrom: Sam"
    assert strip_quoted(text) == "Thanks!"


def test_reply_address_uses_address_domain():
    assert reply_address(ADDRESS, "conv-123") == "reply+conv-123@acme.com"


# --- inbound ----------------------------------------------------------------


async def test_inbound_new_conversation(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _email_inbox()
    response = await client.post(
        "/api/channels/email/inbound",
        json={
            "to": ADDRESS,
            "from": "Jane Doe <jane@example.com>",
            "subject": "Help please",
            "text": "I can't log in.",
            "message_id": "<msg-1@example.com>",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "created"
    conversation_id = body["conversation_id"]

    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.inbox_id == inbox_id
    assert await _message_count(conversation_id) == 1


async def test_inbound_routes_by_reply_plus_uuid(client: httpx.AsyncClient):
    _workspace_id, _inbox_id = await _email_inbox()
    first = await client.post(
        "/api/channels/email/inbound",
        json={
            "to": ADDRESS,
            "from": "jane@example.com",
            "text": "first",
            "message_id": "<m1@example.com>",
        },
    )
    conversation_id = first.json()["conversation_id"]

    second = await client.post(
        "/api/channels/email/inbound",
        json={
            "to": f"reply+{conversation_id}@acme.com",
            "from": "jane@example.com",
            "text": "second reply",
            "message_id": "<m2@example.com>",
        },
    )
    assert second.json()["status"] == "appended"
    assert second.json()["conversation_id"] == conversation_id
    assert await _message_count(conversation_id) == 2


async def test_inbound_routes_by_in_reply_to(client: httpx.AsyncClient):
    _workspace_id, _inbox_id = await _email_inbox()
    first = await client.post(
        "/api/channels/email/inbound",
        json={
            "to": ADDRESS,
            "from": "jane@example.com",
            "text": "original",
            "message_id": "<orig@example.com>",
        },
    )
    conversation_id = first.json()["conversation_id"]

    second = await client.post(
        "/api/channels/email/inbound",
        json={
            "to": "someone-else@acme.com",
            "from": "jane@example.com",
            "text": "threaded",
            "message_id": "<threaded@example.com>",
            "in_reply_to": "<orig@example.com>",
        },
    )
    assert second.json()["conversation_id"] == conversation_id


async def test_inbound_strips_quoted_tail(client: httpx.AsyncClient):
    _workspace_id, _inbox_id = await _email_inbox()
    response = await client.post(
        "/api/channels/email/inbound",
        json={
            "to": ADDRESS,
            "from": "jane@example.com",
            "text": "Just this line\n\nOn Tue Sam <s@acme.com> wrote:\n> quoted stuff",
            "message_id": "<q1@example.com>",
        },
    )
    conversation_id = response.json()["conversation_id"]
    async with get_session_factory()() as session:
        message = (
            (
                await session.execute(
                    select(Message).where(
                        Message.conversation_id == conversation_id,
                        Message.direction == MessageDirection.IN,
                    )
                )
            )
            .scalars()
            .first()
        )
        assert message is not None
        assert message.content == "Just this line"


async def test_inbound_dedupes_by_message_id(client: httpx.AsyncClient):
    _workspace_id, _inbox_id = await _email_inbox()
    payload = {
        "to": ADDRESS,
        "from": "jane@example.com",
        "text": "dup",
        "message_id": "<dup@example.com>",
    }
    first = await client.post("/api/channels/email/inbound", json=payload)
    await client.post("/api/channels/email/inbound", json=payload)
    conversation_id = first.json()["conversation_id"]
    assert await _message_count(conversation_id) == 1


async def test_inbound_no_matching_inbox_ignored(client: httpx.AsyncClient):
    await _email_inbox()
    response = await client.post(
        "/api/channels/email/inbound",
        json={"to": "unknown@nowhere.com", "from": "jane@example.com", "text": "hi"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


# --- outbound ---------------------------------------------------------------


async def test_outbound_email_sender(client: httpx.AsyncClient, monkeypatch):
    workspace_id, inbox_id = await _email_inbox()
    conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_email="jane@example.com", subject="Login issue"
    )

    calls: list[dict] = []

    async def fake_send_email(
        to, subject, html, *, reply_to=None, from_override=None, headers=None
    ):
        calls.append(
            {
                "to": to,
                "subject": subject,
                "reply_to": reply_to,
                "from_override": from_override,
                "headers": headers,
            }
        )
        return True

    monkeypatch.setattr("app.channels.email.send_email", fake_send_email)

    from app.channels.email import send
    from app.models.inbox import Inbox

    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        message = await session.get(Message, message_id)
        await send(session, inbox, message)

    assert len(calls) == 1
    call = calls[0]
    assert call["to"] == "jane@example.com"
    assert call["from_override"] == ADDRESS
    assert call["reply_to"] == reply_address(ADDRESS, conversation_id)
    assert call["subject"] == "Re: Login issue"


async def test_outbound_email_raises_on_failure(client: httpx.AsyncClient, monkeypatch):
    workspace_id, inbox_id = await _email_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_email="jane@example.com"
    )

    async def failing_send_email(*args, **kwargs):
        return False

    monkeypatch.setattr("app.channels.email.send_email", failing_send_email)

    from app.channels.email import send
    from app.models.inbox import Inbox

    with pytest.raises(RuntimeError):
        async with get_session_factory()() as session:
            inbox = await session.get(Inbox, inbox_id)
            message = await session.get(Message, message_id)
            await send(session, inbox, message)
