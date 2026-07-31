"""Inbound Twilio SMS webhooks.

One webhook URL per inbox (``/webhook/{inbox_id}``) for inbound messages and one
status callback (``/status/{inbox_id}``) for delivery receipts. Both are
form-encoded posts validated against ``X-Twilio-Signature`` (HMAC-SHA1 of the
exact URL + sorted params, keyed by the inbox's auth token). Importing this
module also registers the outbound SMS sender.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.channels.sms  # noqa: F401 — registers the outbound "sms" sender
from app.channels.sms import compute_twilio_signature
from app.core.config import get_settings
from app.core.deps import Db
from app.core.errors import NotFoundError, UnauthorizedError
from app.models.conversation import Conversation
from app.models.inbox import ChannelType, Inbox
from app.models.message import DeliveryStatus, Message, MessageDirection
from app.services import conversations as conversations_service
from app.services.inboxes import get_secrets

router = APIRouter()

TWIML_EMPTY = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


async def _get_sms_inbox(session: AsyncSession, inbox_id: str) -> Inbox:
    inbox = await session.get(Inbox, inbox_id)
    if inbox is None or inbox.channel_type != ChannelType.SMS or not inbox.enabled:
        raise NotFoundError("SMS inbox not found")
    return inbox


async def _validated_form(request: Request, inbox: Inbox) -> dict[str, str]:
    """Parse the form post and verify ``X-Twilio-Signature`` over the exact URL
    Twilio called (public_base_url + path) plus all POST params."""
    form = await request.form()
    params = {key: value for key, value in form.multi_items() if isinstance(value, str)}
    auth_token = get_secrets(inbox).get("auth_token") or ""
    signature = request.headers.get("X-Twilio-Signature") or ""
    url = get_settings().public_base_url + request.url.path
    expected = compute_twilio_signature(auth_token, url, params)
    if not auth_token or not signature or not hmac.compare_digest(expected, signature):
        raise UnauthorizedError("Invalid Twilio signature")
    return params


@router.post("/webhook/{inbox_id}")
async def sms_webhook(inbox_id: str, request: Request, session: Db) -> Response:
    inbox = await _get_sms_inbox(session, inbox_id)
    params = await _validated_form(request, inbox)

    from_number = params.get("From") or ""
    if from_number:
        body = params.get("Body") or ""
        try:
            num_media = int(params.get("NumMedia") or "0")
        except ValueError:
            num_media = 0
        content = body or ("[Media message]" if num_media > 0 else "")
        name = params.get("ProfileName") or from_number
        await conversations_service.ingest_inbound(
            session,
            inbox,
            source_id=from_number,
            content=content,
            contact_info={
                "name": name,
                "external_id": f"sms:{from_number}",
                "attributes": {"phone": from_number},
            },
            message_source_id=params.get("SmsSid") or None,
            meta={"sms_to": params.get("To"), "sms_num_media": num_media},
        )
    return Response(content=TWIML_EMPTY, media_type="application/xml")


@router.post("/status/{inbox_id}")
async def sms_status(inbox_id: str, request: Request, session: Db) -> dict[str, Any]:
    inbox = await _get_sms_inbox(session, inbox_id)
    params = await _validated_form(request, inbox)

    message_sid = params.get("MessageSid") or ""
    status = (params.get("MessageStatus") or "").lower()
    if not message_sid:
        return {"ok": True}
    message = (
        (
            await session.execute(
                select(Message)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(
                    Conversation.workspace_id == inbox.workspace_id,
                    Message.direction == MessageDirection.OUT,
                    Message.source_id == message_sid,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if message is None:
        return {"ok": True}  # unknown sid — nothing to update
    if status in {"sent", "delivered"}:
        message.delivery_status = DeliveryStatus.SENT
        message.delivery_error = None
    elif status in {"failed", "undelivered"}:
        message.delivery_status = DeliveryStatus.FAILED
        error_code = params.get("ErrorCode") or "unknown"
        message.delivery_error = params.get("ErrorMessage") or f"Twilio error {error_code}"
    return {"ok": True}
