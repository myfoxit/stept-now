"""The channel adapters register outbound senders so ``deliver_message`` routes
to them instead of marking these channel types 'failed'."""

from __future__ import annotations

import httpx
import respx
from sqlalchemy import select

from app.channels.slack import POST_MESSAGE_URL
from app.core.db import get_session_factory
from app.core.queue import run_task
from app.models.message import Message
from tests.channels.conftest import make_inbox, make_outbound_message, make_workspace


async def _mark_pending(message_id: str) -> None:
    async with get_session_factory()() as session:
        message = await session.get(Message, message_id)
        assert message is not None
        message.delivery_status = "pending"
        await session.commit()


async def _reload(message_id: str) -> Message:
    async with get_session_factory()() as session:
        return (await session.execute(select(Message).where(Message.id == message_id))).scalar_one()


async def test_all_channel_senders_registered(client: httpx.AsyncClient):
    from app.channels.registry import SENDERS

    assert {"email", "slack", "telegram"} <= set(SENDERS)


async def test_deliver_message_email_sent(client: httpx.AsyncClient):
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id, channel_type="email", config={"address": "support@acme.com"}
    )
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_email="jane@example.com"
    )
    await _mark_pending(message_id)

    await run_task("deliver_message", {"message_id": message_id})

    message = await _reload(message_id)
    assert message.delivery_status == "sent"
    assert message.delivery_error != "no sender registered"


@respx.mock
async def test_deliver_message_slack_sent(client: httpx.AsyncClient):
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id,
        channel_type="slack",
        secrets={"bot_token": "xoxb-1", "signing_secret": "s"},
    )
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id="C1:thread"
    )
    await _mark_pending(message_id)
    respx.post(POST_MESSAGE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    await run_task("deliver_message", {"message_id": message_id})

    message = await _reload(message_id)
    assert message.delivery_status == "sent"
    assert message.delivery_error is None


@respx.mock
async def test_deliver_message_telegram_sent(client: httpx.AsyncClient):
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id, channel_type="telegram", secrets={"bot_token": "123:ABC"}
    )
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id="555"
    )
    await _mark_pending(message_id)
    respx.post("https://api.telegram.org/bot123:ABC/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await run_task("deliver_message", {"message_id": message_id})

    message = await _reload(message_id)
    assert message.delivery_status == "sent"
    assert message.delivery_error != "no sender registered"
