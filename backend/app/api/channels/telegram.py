"""Inbound Telegram webhook.

One webhook URL per inbox (``/webhook/{inbox_id}``) with a shared ``?secret=``
that must match the inbox's configured ``webhook_secret``. Text message updates
are ingested as inbound messages keyed by chat id. Importing this module also
registers the outbound Telegram sender.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

import app.channels.telegram  # noqa: F401 — registers the outbound "telegram" sender
from app.core.deps import Db
from app.core.errors import NotFoundError, UnauthorizedError
from app.models.inbox import ChannelType, Inbox
from app.services import conversations as conversations_service

router = APIRouter()


async def _get_telegram_inbox(session: AsyncSession, inbox_id: str) -> Inbox:
    inbox = await session.get(Inbox, inbox_id)
    if inbox is None or inbox.channel_type != ChannelType.TELEGRAM or not inbox.enabled:
        raise NotFoundError("Telegram inbox not found")
    return inbox


@router.post("/webhook/{inbox_id}")
async def telegram_webhook(
    inbox_id: str, update: dict[str, Any], session: Db, secret: str = ""
) -> dict[str, Any]:
    inbox = await _get_telegram_inbox(session, inbox_id)
    expected = inbox.config.get("webhook_secret") or ""
    if not expected or not hmac.compare_digest(str(expected), secret):
        raise UnauthorizedError("Invalid webhook secret")

    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict) or "text" not in message:
        return {"ok": True}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True}
    source_id = str(chat_id)

    sender = message.get("from") or {}
    name = sender.get("first_name") or sender.get("username") or f"Telegram {source_id}"
    external_id = f"telegram:{sender['id']}" if sender.get("id") is not None else None

    await conversations_service.ingest_inbound(
        session,
        inbox,
        source_id=source_id,
        content=message.get("text") or "",
        contact_info={"name": name, "external_id": external_id},
        message_source_id=f"{source_id}:{message.get('message_id')}",
        meta={"telegram_chat_id": chat_id, "telegram_user": sender.get("id")},
    )
    return {"ok": True}
