"""Meta Messenger + Instagram DM channel adapters: outbound senders + shared helpers.

Messenger — config: ``{page_id, webhook_verify_token}``; secrets:
``{page_access_token, app_secret}``. Instagram — config: ``{instagram_id,
webhook_verify_token}``; secrets: ``{access_token, app_secret}``. The recipient
PSID/IGSID is the conversation's ``ContactInbox.source_id``.

``verify_meta_signature`` implements Meta's shared ``X-Hub-Signature-256``
webhook scheme and is also used by the WhatsApp webhook.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import http_client
from app.channels.registry import register_sender
from app.models.conversation import Conversation
from app.models.inbox import ContactInbox, Inbox
from app.models.message import Message
from app.services.inboxes import get_secrets

MESSENGER_SEND_URL = "https://graph.facebook.com/v19.0/me/messages"
INSTAGRAM_SEND_URL = "https://graph.instagram.com/v22.0/me/messages"


def verify_meta_signature(
    raw: bytes, header: str | None, app_secret: str | None, *, allow_unsigned: bool = False
) -> bool:
    """Meta webhook scheme: ``X-Hub-Signature-256`` = ``sha256=`` + HMAC-SHA256 of
    the raw request body keyed by the app secret (constant-time compare).

    Fails **closed**: with no stored app secret the request is rejected, because
    this endpoint is public and an accepted unsigned body is a spoofed inbound
    message. Relay setups that genuinely have no Meta app secret (360dialog and
    friends) opt in per inbox with ``config.allow_unsigned = true``, which is an
    explicit, auditable choice rather than the default.
    """
    if not app_secret:
        return allow_unsigned
    if not header:
        return False
    digest = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", header)


def parse_graph_send_response(response: httpx.Response, channel: str) -> dict[str, Any]:
    """Parse a Graph send response; raise with Meta's ``error.message`` on failure."""
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        data = {}
    error = data.get("error")
    if response.status_code >= 400 or error:
        detail = error.get("message") if isinstance(error, dict) else None
        raise RuntimeError(f"{channel} send failed: {detail or f'HTTP {response.status_code}'}")
    return data


async def _recipient_id(session: AsyncSession, message: Message, channel: str) -> str:
    conversation = await session.get(Conversation, message.conversation_id)
    contact_inbox = (
        await session.get(ContactInbox, conversation.contact_inbox_id)
        if conversation is not None and conversation.contact_inbox_id is not None
        else None
    )
    recipient = contact_inbox.source_id if contact_inbox is not None else None
    if not recipient:
        raise RuntimeError(f"conversation is not linked to a {channel} user")
    return recipient


@register_sender("messenger")
async def send(session: AsyncSession, inbox: Inbox, message: Message) -> None:
    psid = await _recipient_id(session, message, "messenger")
    page_access_token = get_secrets(inbox).get("page_access_token")
    if not page_access_token:
        raise RuntimeError("messenger page_access_token is not configured")

    async with http_client() as client:
        response = await client.post(
            MESSENGER_SEND_URL,
            params={"access_token": page_access_token},
            json={
                "recipient": {"id": psid},
                "message": {"text": message.content},
                "messaging_type": "RESPONSE",
            },
        )
    data = parse_graph_send_response(response, "messenger")
    if data.get("message_id"):
        message.source_id = str(data["message_id"])


@register_sender("instagram")
async def send_instagram(session: AsyncSession, inbox: Inbox, message: Message) -> None:
    igsid = await _recipient_id(session, message, "instagram")
    access_token = get_secrets(inbox).get("access_token")
    if not access_token:
        raise RuntimeError("instagram access_token is not configured")

    async with http_client() as client:
        response = await client.post(
            INSTAGRAM_SEND_URL,
            params={"access_token": access_token},
            json={"recipient": {"id": igsid}, "message": {"text": message.content}},
        )
    data = parse_graph_send_response(response, "instagram")
    if data.get("message_id"):
        message.source_id = str(data["message_id"])
