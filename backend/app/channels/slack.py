"""Slack channel adapter: request signature verification + outbound sender.

Secrets: ``{bot_token, signing_secret}``. Outbound replies go to the Slack
thread encoded in the conversation's ``ContactInbox.source_id`` (``"{channel}:
{thread_ts}"``) via ``chat.postMessage``.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import http_client
from app.channels.registry import register_sender
from app.models.conversation import Conversation
from app.models.inbox import ContactInbox, Inbox
from app.models.message import Message
from app.services.inboxes import get_secrets

POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
MAX_SKEW_SECONDS = 60 * 5


def verify_signature(
    signing_secret: str, timestamp: str | None, body: bytes, signature: str | None
) -> bool:
    """Slack v0 signing scheme: HMAC-SHA256 of ``v0:{ts}:{body}``; reject stale
    timestamps (> 5 min skew)."""
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > MAX_SKEW_SECONDS:
        return False
    basestring = b"v0:" + timestamp.encode() + b":" + body
    digest = hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"v0={digest}", signature)


@register_sender("slack")
async def send(session: AsyncSession, inbox: Inbox, message: Message) -> None:
    conversation = await session.get(Conversation, message.conversation_id)
    contact_inbox = (
        await session.get(ContactInbox, conversation.contact_inbox_id)
        if conversation is not None and conversation.contact_inbox_id is not None
        else None
    )
    source_id = contact_inbox.source_id if contact_inbox is not None else None
    if not source_id or ":" not in source_id:
        raise RuntimeError("conversation is not linked to a slack thread")
    channel, thread_ts = source_id.split(":", 1)

    bot_token = get_secrets(inbox).get("bot_token")
    if not bot_token:
        raise RuntimeError("slack bot_token is not configured")

    async with http_client() as client:
        response = await client.post(
            POST_MESSAGE_URL,
            headers={"Authorization": f"Bearer {bot_token}"},
            json={"channel": channel, "thread_ts": thread_ts, "text": message.content},
        )
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"slack chat.postMessage failed: {data.get('error')}")
