"""Inbound LINE messaging webhook.

One webhook URL per inbox (``/webhook/{inbox_id}``). Requests are verified via
``x-line-signature`` (base64 HMAC-SHA256 of the raw body, keyed by the channel
secret). Text message events are ingested keyed by the LINE user id; display
names come from a best-effort profile fetch. Importing this module also
registers the outbound LINE sender.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession

import app.channels.line  # noqa: F401 — registers the outbound "line" sender
from app.channels.line import compute_line_signature, fetch_display_name
from app.core.deps import Db
from app.core.errors import NotFoundError, UnauthorizedError
from app.models.inbox import ChannelType, Inbox
from app.services import conversations as conversations_service
from app.services.inboxes import get_secrets

router = APIRouter()


async def _get_line_inbox(session: AsyncSession, inbox_id: str) -> Inbox:
    inbox = await session.get(Inbox, inbox_id)
    if inbox is None or inbox.channel_type != ChannelType.LINE or not inbox.enabled:
        raise NotFoundError("LINE inbox not found")
    return inbox


@router.post("/webhook/{inbox_id}")
async def line_webhook(inbox_id: str, request: Request, session: Db) -> dict[str, Any]:
    inbox = await _get_line_inbox(session, inbox_id)
    raw = await request.body()

    secrets = get_secrets(inbox)
    channel_secret = secrets.get("channel_secret") or ""
    signature = request.headers.get("x-line-signature") or ""
    expected = compute_line_signature(channel_secret, raw)
    if not channel_secret or not signature or not hmac.compare_digest(expected, signature):
        raise UnauthorizedError("Invalid LINE signature")

    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {"ok": True}

    channel_token = secrets.get("channel_token") or ""
    events = payload.get("events") if isinstance(payload, dict) else None
    for event in events or []:
        if not isinstance(event, dict) or event.get("type") != "message":
            continue
        message = event.get("message") or {}
        if message.get("type") != "text":
            continue  # non-text messages are skipped silently
        user_id = (event.get("source") or {}).get("userId")
        if not user_id:
            continue
        name = await fetch_display_name(channel_token, user_id) or f"LINE user {user_id[-6:]}"
        message_id = message.get("id")
        await conversations_service.ingest_inbound(
            session,
            inbox,
            source_id=user_id,
            content=message.get("text") or "",
            contact_info={"name": name, "external_id": f"line:{user_id}"},
            message_source_id=str(message_id) if message_id is not None else None,
            meta={"line_user": user_id},
        )
    return {"ok": True}
