"""Telegram channel adapter: outbound ``sendMessage`` sender.

Secrets: ``{bot_token}``. Config: ``{webhook_secret}``. The chat id is the
conversation's ``ContactInbox.source_id``. The webhook itself is registered
manually via Telegram's ``setWebhook`` (documented in the channel settings UI),
so there is no setup API here.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import http_client
from app.channels.registry import register_sender
from app.models.conversation import Conversation
from app.models.inbox import ContactInbox, Inbox
from app.models.message import Message
from app.services.inboxes import get_secrets

API_BASE = "https://api.telegram.org"


@register_sender("telegram")
async def send(session: AsyncSession, inbox: Inbox, message: Message) -> None:
    conversation = await session.get(Conversation, message.conversation_id)
    contact_inbox = (
        await session.get(ContactInbox, conversation.contact_inbox_id)
        if conversation is not None and conversation.contact_inbox_id is not None
        else None
    )
    chat_id = contact_inbox.source_id if contact_inbox is not None else None
    if not chat_id:
        raise RuntimeError("conversation is not linked to a telegram chat")

    bot_token = get_secrets(inbox).get("bot_token")
    if not bot_token:
        raise RuntimeError("telegram bot_token is not configured")

    async with http_client() as client:
        response = await client.post(
            f"{API_BASE}/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message.content},
        )
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram sendMessage failed: {data.get('description')}")
