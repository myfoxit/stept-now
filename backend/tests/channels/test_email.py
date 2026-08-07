"""Email channel: inbound routing + quoted-tail stripping + dedupe, outbound
dispatch through send_via_inbox, legacy-endpoint token enforcement."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select

from app.channels.email import reply_address, strip_quoted
from app.channels.email_transports import EmailDeliveryError
from app.core.db import get_session_factory, uuid7
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.message import Message, MessageDirection
from tests.channels.conftest import make_inbox, make_outbound_message, make_workspace

ADDRESS = "support@acme.com"


async def _email_inbox(config: dict | None = None) -> tuple[str, str, str]:
    """Returns (workspace_id, inbox_id, webhook_token)."""
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id, channel_type="email", config=config or {"address": ADDRESS}, name="Email"
    )
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        token = inbox.config["webhook_token"]
    return workspace_id, inbox_id, token


async def _legacy_inbox_without_token() -> tuple[str, str]:
    """Pre-W11 email inbox: no webhook_token in config (raw row, not service)."""
    workspace_id = await make_workspace()
    async with get_session_factory()() as session:
        inbox = Inbox(
            workspace_id=workspace_id,
            name="Legacy email",
            channel_type="email",
            enabled=True,
            config={"address": ADDRESS},
        )
        session.add(inbox)
        await session.commit()
        return workspace_id, inbox.id


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


# --- inbound (legacy bare endpoint, with token) ------------------------------


async def test_inbound_new_conversation(client: httpx.AsyncClient):
    _workspace_id, inbox_id, token = await _email_inbox()
    response = await client.post(
        f"/api/channels/email/inbound?token={token}",
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
    _workspace_id, _inbox_id, token = await _email_inbox()
    first = await client.post(
        f"/api/channels/email/inbound?token={token}",
        json={
            "to": ADDRESS,
            "from": "jane@example.com",
            "text": "first",
            "message_id": "<m1@example.com>",
        },
    )
    conversation_id = first.json()["conversation_id"]

    second = await client.post(
        f"/api/channels/email/inbound?token={token}",
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
    _workspace_id, _inbox_id, token = await _email_inbox()
    first = await client.post(
        f"/api/channels/email/inbound?token={token}",
        json={
            "to": ADDRESS,
            "from": "jane@example.com",
            "text": "original",
            "message_id": "<orig@example.com>",
        },
    )
    conversation_id = first.json()["conversation_id"]

    second = await client.post(
        f"/api/channels/email/inbound?token={token}",
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
    _workspace_id, _inbox_id, token = await _email_inbox()
    response = await client.post(
        f"/api/channels/email/inbound?token={token}",
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


async def test_inbound_html_only_body_converted_to_text(client: httpx.AsyncClient):
    _workspace_id, _inbox_id, token = await _email_inbox()
    response = await client.post(
        f"/api/channels/email/inbound?token={token}",
        json={
            "to": ADDRESS,
            "from": "jane@example.com",
            "text": "",
            "html": "<p>Hello <b>there</b></p>",
            "message_id": "<h1@example.com>",
        },
    )
    conversation_id = response.json()["conversation_id"]
    async with get_session_factory()() as session:
        message = (
            (
                await session.execute(
                    select(Message).where(Message.conversation_id == conversation_id)
                )
            )
            .scalars()
            .first()
        )
        assert message.content == "Hello there"


async def test_inbound_dedupes_by_message_id(client: httpx.AsyncClient):
    _workspace_id, _inbox_id, token = await _email_inbox()
    payload = {
        "to": ADDRESS,
        "from": "jane@example.com",
        "text": "dup",
        "message_id": "<dup@example.com>",
    }
    first = await client.post(f"/api/channels/email/inbound?token={token}", json=payload)
    await client.post(f"/api/channels/email/inbound?token={token}", json=payload)
    conversation_id = first.json()["conversation_id"]
    assert await _message_count(conversation_id) == 1


async def test_inbound_no_matching_inbox_404(client: httpx.AsyncClient):
    """The open relay is closed: unresolvable mail 404s instead of 200-ignored."""
    await _email_inbox()
    response = await client.post(
        "/api/channels/email/inbound",
        json={"to": "unknown@nowhere.com", "from": "jane@example.com", "text": "hi"},
    )
    assert response.status_code == 404


async def test_inbound_legacy_requires_token_once_set(client: httpx.AsyncClient):
    _workspace_id, _inbox_id, _token = await _email_inbox()
    response = await client.post(
        "/api/channels/email/inbound",
        json={"to": ADDRESS, "from": "jane@example.com", "text": "hi"},
    )
    assert response.status_code == 404
    wrong = await client.post(
        "/api/channels/email/inbound?token=wrong",
        json={"to": ADDRESS, "from": "jane@example.com", "text": "hi"},
    )
    assert wrong.status_code == 404


async def test_inbound_legacy_tokenless_inbox_keeps_working(client: httpx.AsyncClient):
    """Pre-W11 inbox rows without a webhook_token accept bare posts (one release
    of back-compat)."""
    _workspace_id, inbox_id = await _legacy_inbox_without_token()
    response = await client.post(
        "/api/channels/email/inbound",
        json={"to": ADDRESS, "from": "jane@example.com", "text": "hi"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "created"
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, response.json()["conversation_id"])
        assert conversation.inbox_id == inbox_id


async def test_inbound_matches_forward_to_address(client: httpx.AsyncClient):
    _workspace_id, inbox_id, token = await _email_inbox(
        {"address": ADDRESS, "forward_to": "in-abc123@parse.stept.dev"}
    )
    response = await client.post(
        f"/api/channels/email/inbound?token={token}",
        json={"to": "in-abc123@parse.stept.dev", "from": "jane@example.com", "text": "fwd"},
    )
    assert response.status_code == 200, response.text
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, response.json()["conversation_id"])
        assert conversation.inbox_id == inbox_id


# --- inbound (token-secured generic endpoint) --------------------------------


async def test_tokened_endpoint_creates_conversation(client: httpx.AsyncClient):
    _workspace_id, inbox_id, token = await _email_inbox()
    response = await client.post(
        f"/api/channels/email/inbound/{inbox_id}/{token}",
        json={
            "to": "anything@anywhere.com",  # inbox-bound: recipients need not match
            "from": "jane@example.com",
            "text": "via tokened endpoint",
        },
    )
    assert response.status_code == 200, response.text
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, response.json()["conversation_id"])
        assert conversation.inbox_id == inbox_id


async def test_tokened_endpoint_rejects_bad_token(client: httpx.AsyncClient):
    _workspace_id, inbox_id, _token = await _email_inbox()
    response = await client.post(
        f"/api/channels/email/inbound/{inbox_id}/not-the-token",
        json={"to": ADDRESS, "from": "jane@example.com", "text": "hi"},
    )
    assert response.status_code == 404
    missing = await client.post(
        f"/api/channels/email/inbound/{uuid7()}/whatever",
        json={"to": ADDRESS, "from": "jane@example.com", "text": "hi"},
    )
    assert missing.status_code == 404


async def test_tokened_endpoint_rejects_disabled_inbox(client: httpx.AsyncClient):
    _workspace_id, inbox_id, token = await _email_inbox()
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        inbox.enabled = False
        await session.commit()
    response = await client.post(
        f"/api/channels/email/inbound/{inbox_id}/{token}",
        json={"to": ADDRESS, "from": "jane@example.com", "text": "hi"},
    )
    assert response.status_code == 404


async def test_tokened_endpoint_ignores_cross_workspace_reply_address(
    client: httpx.AsyncClient,
):
    """A forged reply+{id} for another workspace's conversation must not append
    there — the path token only authenticates one inbox."""
    _ws_a, _inbox_a, token_a = await _email_inbox()
    first = await client.post(
        f"/api/channels/email/inbound?token={token_a}",
        json={"to": ADDRESS, "from": "jane@example.com", "text": "hello"},
    )
    victim_conversation = first.json()["conversation_id"]

    _ws_b, inbox_b_id, token_b = await _email_inbox({"address": "help@other.io"})
    response = await client.post(
        f"/api/channels/email/inbound/{inbox_b_id}/{token_b}",
        json={
            "to": f"reply+{victim_conversation}@acme.com",
            "from": "mallory@example.com",
            "text": "injected",
        },
    )
    assert response.status_code == 200
    assert response.json()["conversation_id"] != victim_conversation
    assert await _message_count(victim_conversation) == 1


# --- outbound ---------------------------------------------------------------


async def test_outbound_email_sender(client: httpx.AsyncClient, monkeypatch):
    workspace_id, inbox_id, _token = await _email_inbox()
    conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_email="jane@example.com", subject="Login issue"
    )

    calls: list[dict] = []

    async def fake_send_via_inbox(session, inbox, *, to, subject, html, text, reply_to, headers):
        calls.append(
            {
                "to": to,
                "subject": subject,
                "html": html,
                "text": text,
                "reply_to": reply_to,
                "headers": headers,
            }
        )
        return "provider-123"

    monkeypatch.setattr("app.channels.email.send_via_inbox", fake_send_via_inbox)

    from app.channels.email import send

    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        message = await session.get(Message, message_id)
        await send(session, inbox, message)
        await session.commit()

    assert len(calls) == 1
    call = calls[0]
    assert call["to"] == "jane@example.com"
    assert call["reply_to"] == reply_address(ADDRESS, conversation_id)
    assert call["subject"] == "Re: Login issue"
    assert call["headers"]["Message-ID"].endswith("@acme.com>")

    async with get_session_factory()() as session:
        message = await session.get(Message, message_id)
        assert message.meta["message_id"] == call["headers"]["Message-ID"]
        assert message.meta["provider_message_id"] == "provider-123"
        assert message.source_id == call["headers"]["Message-ID"]


async def test_outbound_email_raises_on_failure(client: httpx.AsyncClient, monkeypatch):
    workspace_id, inbox_id, _token = await _email_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_email="jane@example.com"
    )

    async def failing_send_via_inbox(*args, **kwargs):
        raise EmailDeliveryError("email delivery failed")

    monkeypatch.setattr("app.channels.email.send_via_inbox", failing_send_via_inbox)

    from app.channels.email import send

    with pytest.raises(EmailDeliveryError):
        async with get_session_factory()() as session:
            inbox = await session.get(Inbox, inbox_id)
            message = await session.get(Message, message_id)
            await send(session, inbox, message)
