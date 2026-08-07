"""Message-ID / In-Reply-To / References over a full 3-message conversation:
inbound → outbound → customer reply threads back → next outbound chains."""

from __future__ import annotations

import re

import httpx

from app.channels.email import send
from app.core.db import get_session_factory
from app.core.events import Actor
from app.models.inbox import Inbox
from app.models.message import Message
from app.services import conversations as conversations_service
from tests.channels.conftest import make_inbox, make_workspace

ADDRESS = "support@acme.com"
MESSAGE_ID_RE = re.compile(r"^<[0-9a-f-]+@acme\.com>$")


async def _email_inbox() -> tuple[str, str, str]:
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id, channel_type="email", config={"address": ADDRESS}, name="Email"
    )
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        token = inbox.config["webhook_token"]
    return workspace_id, inbox_id, token


async def _add_reply(conversation_id: str, content: str) -> str:
    async with get_session_factory()() as session:
        conversation = await session.get(conversations_service.Conversation, conversation_id)
        message = await conversations_service.add_message(
            session,
            conversation,
            direction="out",
            author_type="user",
            author_id=None,
            author_name="Sam Support",
            content=content,
            actor=Actor.system(),
            deliver=False,
        )
        await session.commit()
        return message.id


async def _deliver(inbox_id: str, message_id: str, sent: list[dict]) -> None:
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        message = await session.get(Message, message_id)
        await send(session, inbox, message)
        await session.commit()
    del sent  # captured by the monkeypatched transport


async def test_threading_chain_over_three_messages(client: httpx.AsyncClient, monkeypatch):
    _workspace_id, inbox_id, token = await _email_inbox()
    sent: list[dict] = []

    async def fake_send_via_inbox(session, inbox, **kwargs):
        sent.append(kwargs)
        return None

    monkeypatch.setattr("app.channels.email.send_via_inbox", fake_send_via_inbox)

    # 1) inbound from the customer
    first = await client.post(
        f"/api/channels/email/inbound?token={token}",
        json={
            "to": ADDRESS,
            "from": "jane@example.com",
            "subject": "Login issue",
            "text": "I cannot log in.",
            "message_id": "<cust-1@example.com>",
        },
    )
    conversation_id = first.json()["conversation_id"]

    # 2) our reply: fresh Message-ID, threads off the inbound
    out1 = await _add_reply(conversation_id, "Try resetting your password.")
    await _deliver(inbox_id, out1, sent)
    headers1 = sent[0]["headers"]
    assert MESSAGE_ID_RE.match(headers1["Message-ID"])
    assert headers1["In-Reply-To"] == "<cust-1@example.com>"
    assert headers1["References"] == "<cust-1@example.com>"
    out1_message_id = headers1["Message-ID"]

    async with get_session_factory()() as session:
        stored = await session.get(Message, out1)
        assert stored.meta["message_id"] == out1_message_id
        assert stored.source_id == out1_message_id

    # 3) the customer replies to OUR message id — routes into the same thread
    second = await client.post(
        f"/api/channels/email/inbound?token={token}",
        json={
            "to": "jane-mua-random@example.com",  # MUAs reply to Reply-To or From
            "from": "jane@example.com",
            "text": "Still broken.",
            "message_id": "<cust-2@example.com>",
            "in_reply_to": out1_message_id,
        },
    )
    assert second.json() == {"status": "appended", "conversation_id": conversation_id}

    # 4) next outbound chains: our last outbound id + the latest inbound id
    out2 = await _add_reply(conversation_id, "Escalating this now.")
    await _deliver(inbox_id, out2, sent)
    headers2 = sent[1]["headers"]
    assert headers2["In-Reply-To"] == "<cust-2@example.com>"
    assert headers2["References"] == f"{out1_message_id} <cust-2@example.com>"
    assert headers2["Message-ID"] != out1_message_id


async def test_message_id_uses_fallback_domain_without_address(
    client: httpx.AsyncClient, monkeypatch
):
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(workspace_id, channel_type="email", config={}, name="Email")
    sent: list[dict] = []

    async def fake_send_via_inbox(session, inbox, **kwargs):
        sent.append(kwargs)
        return None

    monkeypatch.setattr("app.channels.email.send_via_inbox", fake_send_via_inbox)

    from tests.channels.conftest import make_outbound_message

    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_email="jane@example.com"
    )
    await _deliver(inbox_id, message_id, sent)
    assert sent[0]["headers"]["Message-ID"].endswith("@stept.local>")
    assert sent[0]["reply_to"] is None
