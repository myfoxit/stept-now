"""Inbound WhatsApp Cloud API webhook.

One webhook URL per inbox (``/webhook/{inbox_id}``). ``GET`` answers Meta's
verify challenge: ``hub.verify_token`` must match the inbox's configured
``webhook_verify_token`` and the raw ``hub.challenge`` is echoed back. ``POST``
receives message and delivery-status payloads (root =
``entry[].changes[].value``).

Signature: the ``X-Hub-Signature-256`` header (``sha256=`` + HMAC-SHA256 of the
raw body) is required and verified against the inbox's stored ``app_secret``.
With no stored app secret the request is **rejected** — this endpoint is public,
so an accepted unsigned body is a spoofed inbound message. Relay setups that
have no Meta app secret (360dialog and friends) opt in per inbox with
``config.allow_unsigned = true``. Parseable payloads always answer 200 (Meta
retries on non-2xx); unknown shapes no-op. Importing this module also registers
the outbound WhatsApp sender.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.channels.whatsapp  # noqa: F401 — registers the outbound "whatsapp" sender
from app.channels.messenger import verify_meta_signature
from app.core.deps import Db
from app.core.errors import NotFoundError, UnauthorizedError
from app.models.conversation import Conversation
from app.models.inbox import ChannelType, Inbox
from app.models.message import DeliveryStatus, Message
from app.services import conversations as conversations_service
from app.services.inboxes import get_secrets

router = APIRouter()

_SENT_STATUSES = {"sent", "delivered", "read"}
_SKIPPED_TYPES = {"reaction", "ephemeral"}


async def _get_whatsapp_inbox(session: AsyncSession, inbox_id: str) -> Inbox:
    inbox = await session.get(Inbox, inbox_id)
    if inbox is None or inbox.channel_type != ChannelType.WHATSAPP or not inbox.enabled:
        raise NotFoundError("WhatsApp inbox not found")
    return inbox


@router.get("/webhook/{inbox_id}")
async def whatsapp_verify(
    inbox_id: str,
    session: Db,
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
) -> PlainTextResponse:
    inbox = await _get_whatsapp_inbox(session, inbox_id)
    expected = str(inbox.config.get("webhook_verify_token") or "")
    if not expected or not hmac.compare_digest(expected, hub_verify_token):
        raise UnauthorizedError("Invalid verify token")
    return PlainTextResponse(hub_challenge)


@router.post("/webhook/{inbox_id}")
async def whatsapp_webhook(inbox_id: str, request: Request, session: Db) -> dict[str, Any]:
    inbox = await _get_whatsapp_inbox(session, inbox_id)
    raw = await request.body()
    app_secret = get_secrets(inbox).get("app_secret")
    if not verify_meta_signature(
        raw,
        request.headers.get("X-Hub-Signature-256"),
        app_secret,
        allow_unsigned=bool(inbox.config.get("allow_unsigned")),
    ):
        raise UnauthorizedError("Invalid Meta signature")

    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {"ok": True}
    if not isinstance(payload, dict):
        return {"ok": True}

    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            value = change.get("value") if isinstance(change, dict) else None
            if not isinstance(value, dict):
                continue
            await _apply_statuses(session, inbox, value.get("statuses") or [])
            await _ingest_messages(session, inbox, value)
    return {"ok": True}


async def _apply_statuses(session: AsyncSession, inbox: Inbox, statuses: list[Any]) -> None:
    """Delivery receipts: map onto the outbound message with that wamid."""
    for status in statuses:
        if not isinstance(status, dict):
            continue
        wamid = status.get("id")
        state = status.get("status")
        if not wamid or not state:
            continue
        message = (
            (
                await session.execute(
                    select(Message)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(
                        Conversation.workspace_id == inbox.workspace_id,
                        Message.source_id == str(wamid),
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if message is None:
            continue
        if state in _SENT_STATUSES:
            message.delivery_status = DeliveryStatus.SENT
            message.delivery_error = None
        elif state == "failed":
            errors = status.get("errors") or []
            first = errors[0] if errors and isinstance(errors[0], dict) else {}
            message.delivery_status = DeliveryStatus.FAILED
            message.delivery_error = first.get("title") or "delivery failed"


def _message_content(message: dict[str, Any]) -> str | None:
    """Extract text content; None means the message is skipped."""
    message_type = message.get("type")
    if message_type in _SKIPPED_TYPES:
        return None
    if message_type == "unsupported":
        return "[Unsupported message type]"
    if message_type == "text":
        return (message.get("text") or {}).get("body") or None
    if message_type == "button":
        return (message.get("button") or {}).get("text") or None
    if message_type == "interactive":
        interactive = message.get("interactive") or {}
        button_reply = interactive.get("button_reply") or {}
        list_reply = interactive.get("list_reply") or {}
        return button_reply.get("title") or list_reply.get("title") or None
    return None  # media and other types carry no ingestible text


async def _ingest_messages(session: AsyncSession, inbox: Inbox, value: dict[str, Any]) -> None:
    contacts = value.get("contacts") or []
    contact = contacts[0] if contacts and isinstance(contacts[0], dict) else {}
    profile_name = (contact.get("profile") or {}).get("name")

    for message in value.get("messages") or []:
        if not isinstance(message, dict):
            continue
        sender = message.get("from")
        if not sender:
            continue
        content = _message_content(message)
        if content is None:
            continue
        wa_id = contact.get("wa_id") or sender
        await conversations_service.ingest_inbound(
            session,
            inbox,
            source_id=str(sender),
            content=content,
            contact_info={
                "name": profile_name or f"+{sender}",
                "external_id": f"whatsapp:{wa_id}",
                "attributes": {"phone": f"+{sender}"},
            },
            message_source_id=message.get("id"),
            meta={"whatsapp_wa_id": wa_id, "timestamp": message.get("timestamp")},
        )
