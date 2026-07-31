"""WhatsApp Cloud API channel adapter: outbound text sender.

Config: ``{phone_number_id, business_account_id?, webhook_verify_token}``.
Secrets: ``{api_key (Graph access token), app_secret}``. The customer's msisdn
is the conversation's ``ContactInbox.source_id``.

Free-form replies are only allowed inside Meta's 24-hour customer-service
window, measured from the conversation's latest inbound public message —
outside it the send raises so delivery is marked failed with a clear error
(template messages are the escape hatch and are not implemented here).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import http_client
from app.channels.messenger import parse_graph_send_response
from app.channels.registry import register_sender
from app.core.db import utcnow
from app.models.conversation import Conversation
from app.models.inbox import ContactInbox, Inbox
from app.models.message import Message, MessageDirection, MessageVisibility
from app.services.inboxes import get_secrets

GRAPH_BASE = "https://graph.facebook.com/v19.0"
SESSION_WINDOW = timedelta(hours=24)


async def _inside_session_window(session: AsyncSession, conversation_id: str) -> bool:
    """True when the latest inbound public message is younger than 24h."""
    last_inbound_at = (
        await session.execute(
            select(Message.created_at)
            .where(
                Message.conversation_id == conversation_id,
                Message.direction == MessageDirection.IN,
                Message.visibility == MessageVisibility.PUBLIC,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return last_inbound_at is not None and utcnow() - last_inbound_at <= SESSION_WINDOW


@register_sender("whatsapp")
async def send(session: AsyncSession, inbox: Inbox, message: Message) -> None:
    conversation = await session.get(Conversation, message.conversation_id)
    contact_inbox = (
        await session.get(ContactInbox, conversation.contact_inbox_id)
        if conversation is not None and conversation.contact_inbox_id is not None
        else None
    )
    msisdn = contact_inbox.source_id if contact_inbox is not None else None
    if not msisdn:
        raise RuntimeError("conversation is not linked to a whatsapp number")

    phone_number_id = inbox.config.get("phone_number_id")
    if not phone_number_id:
        raise RuntimeError("whatsapp phone_number_id is not configured")
    api_key = get_secrets(inbox).get("api_key")
    if not api_key:
        raise RuntimeError("whatsapp api_key is not configured")

    if not await _inside_session_window(session, message.conversation_id):
        raise RuntimeError(
            "outside the 24h WhatsApp session window — the customer must message first, "
            "or use a template via Meta"
        )

    async with http_client() as client:
        response = await client.post(
            f"{GRAPH_BASE}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "messaging_product": "whatsapp",
                "to": msisdn,
                "type": "text",
                "text": {"body": message.content},
            },
        )
    data = parse_graph_send_response(response, "whatsapp")
    messages = data.get("messages") or []
    first = messages[0] if messages and isinstance(messages[0], dict) else {}
    if first.get("id"):
        message.source_id = str(first["id"])
