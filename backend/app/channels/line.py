"""LINE channel adapter: webhook signature scheme, profile lookup, push sender.

Secrets: ``{channel_secret, channel_token}``. Config: ``{}``. The LINE user id
is the conversation's ``ContactInbox.source_id``. Contact display names come
from a best-effort profile fetch that must never block ingestion.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import http_client
from app.channels.registry import register_sender
from app.models.conversation import Conversation
from app.models.inbox import ContactInbox, Inbox
from app.models.message import Message
from app.services.inboxes import get_secrets

API_BASE = "https://api.line.me"
PUSH_URL = f"{API_BASE}/v2/bot/message/push"


def compute_line_signature(channel_secret: str, body: bytes) -> str:
    """LINE webhook signature: base64(HMAC-SHA256(channel_secret, raw body))."""
    digest = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


async def fetch_display_name(channel_token: str, user_id: str) -> str | None:
    """Best-effort LINE profile lookup; any failure returns None (ingestion
    must never fail because a profile could not be fetched)."""
    if not channel_token:
        return None
    try:
        async with http_client() as client:
            response = await client.get(
                f"{API_BASE}/v2/bot/profile/{user_id}",
                headers={"Authorization": f"Bearer {channel_token}"},
            )
        if response.status_code != 200:
            return None
        data = response.json()
    except Exception:  # profile lookup is best-effort — swallow everything
        return None
    name = data.get("displayName") if isinstance(data, dict) else None
    return name if isinstance(name, str) and name else None


def _error_detail(status_code: int, data: object) -> str:
    detail: str | None = None
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message:
            detail = message
        details = data.get("details")
        if isinstance(details, list) and details and isinstance(details[0], dict):
            first = details[0].get("message")
            if isinstance(first, str) and first:
                detail = f"{detail}: {first}" if detail else first
    return detail or f"HTTP {status_code}"


@register_sender("line")
async def send(session: AsyncSession, inbox: Inbox, message: Message) -> None:
    conversation = await session.get(Conversation, message.conversation_id)
    contact_inbox = (
        await session.get(ContactInbox, conversation.contact_inbox_id)
        if conversation is not None and conversation.contact_inbox_id is not None
        else None
    )
    user_id = contact_inbox.source_id if contact_inbox is not None else None
    if not user_id:
        raise RuntimeError("conversation is not linked to a line user")

    channel_token = get_secrets(inbox).get("channel_token")
    if not channel_token:
        raise RuntimeError("line channel_token is not configured")

    async with http_client() as client:
        response = await client.post(
            PUSH_URL,
            headers={"Authorization": f"Bearer {channel_token}"},
            json={"to": user_id, "messages": [{"type": "text", "text": message.content}]},
        )
    if response.status_code != 200:
        try:
            data = response.json()
        except ValueError:
            data = None
        raise RuntimeError(f"line push failed: {_error_detail(response.status_code, data)}")
