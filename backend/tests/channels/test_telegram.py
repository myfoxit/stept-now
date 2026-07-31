"""Telegram channel: webhook secret gate, inbound ingestion + dedupe, outbound send."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.core.db import get_session_factory
from app.models.contact import Contact
from app.models.inbox import ContactInbox
from app.models.message import Message
from tests.channels.conftest import make_inbox, make_outbound_message, make_workspace

WEBHOOK_SECRET = "tg-webhook-secret"
BOT_TOKEN = "123456:ABC-TELEGRAM"


async def _telegram_inbox() -> tuple[str, str]:
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id,
        channel_type="telegram",
        config={"webhook_secret": WEBHOOK_SECRET},
        secrets={"bot_token": BOT_TOKEN},
        name="Telegram",
    )
    return workspace_id, inbox_id


def _update(chat_id: int = 555, message_id: int = 1, text: str = "hi from telegram") -> dict:
    return {
        "update_id": 100 + message_id,
        "message": {
            "message_id": message_id,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": 999, "first_name": "Tania", "username": "tania"},
            "text": text,
        },
    }


async def test_webhook_wrong_secret_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _telegram_inbox()
    response = await client.post(
        f"/api/channels/telegram/webhook/{inbox_id}?secret=wrong", json=_update()
    )
    assert response.status_code == 401


async def test_webhook_unknown_inbox_404(client: httpx.AsyncClient):
    response = await client.post(
        f"/api/channels/telegram/webhook/does-not-exist?secret={WEBHOOK_SECRET}", json=_update()
    )
    assert response.status_code == 404


async def test_inbound_creates_conversation(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _telegram_inbox()
    response = await client.post(
        f"/api/channels/telegram/webhook/{inbox_id}?secret={WEBHOOK_SECRET}", json=_update()
    )
    assert response.status_code == 200, response.text

    async with get_session_factory()() as session:
        contact_inbox = (
            await session.execute(select(ContactInbox).where(ContactInbox.inbox_id == inbox_id))
        ).scalar_one()
        assert contact_inbox.source_id == "555"
        contact = await session.get(Contact, contact_inbox.contact_id)
        assert contact is not None
        assert contact.name == "Tania"
        message = (
            (await session.execute(select(Message).where(Message.content == "hi from telegram")))
            .scalars()
            .first()
        )
        assert message is not None


async def test_inbound_dedupes(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _telegram_inbox()
    update = _update(text="dup message")
    await client.post(
        f"/api/channels/telegram/webhook/{inbox_id}?secret={WEBHOOK_SECRET}", json=update
    )
    await client.post(
        f"/api/channels/telegram/webhook/{inbox_id}?secret={WEBHOOK_SECRET}", json=update
    )
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.content == "dup message")
            )
        ).scalar_one()
    assert count == 1


async def test_inbound_ignores_non_text_update(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _telegram_inbox()
    response = await client.post(
        f"/api/channels/telegram/webhook/{inbox_id}?secret={WEBHOOK_SECRET}",
        json={"update_id": 1, "message": {"message_id": 1, "chat": {"id": 1}}},
    )
    assert response.status_code == 200
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(ContactInbox)
                .where(ContactInbox.inbox_id == inbox_id)
            )
        ).scalar_one()
    assert count == 0


@respx.mock
async def test_outbound_sends_message(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _telegram_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id="555", content="agent answer"
    )
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    route = respx.post(url).mock(return_value=httpx.Response(200, json={"ok": True}))

    from app.channels.telegram import send
    from app.models.inbox import Inbox

    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        message = await session.get(Message, message_id)
        await send(session, inbox, message)

    assert route.called
    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"chat_id": "555", "text": "agent answer"}


@respx.mock
async def test_outbound_raises_when_not_ok(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _telegram_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id="555", content="x"
    )
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    respx.post(url).mock(
        return_value=httpx.Response(200, json={"ok": False, "description": "chat not found"})
    )

    from app.channels.telegram import send
    from app.models.inbox import Inbox

    with pytest.raises(RuntimeError):
        async with get_session_factory()() as session:
            inbox = await session.get(Inbox, inbox_id)
            message = await session.get(Message, message_id)
            await send(session, inbox, message)
