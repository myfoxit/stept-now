"""Twilio SMS channel adapter: webhook signature scheme + outbound REST sender.

Secrets: ``{account_sid, auth_token}``. Config: ``{phone_number}`` (the E.164
sender). The contact's phone number is the conversation's
``ContactInbox.source_id``. Delivery receipts arrive on the status callback
registered per message via ``StatusCallback``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import http_client
from app.channels.registry import register_sender
from app.core.config import get_settings
from app.models.conversation import Conversation
from app.models.inbox import ContactInbox, Inbox
from app.models.message import Message
from app.services.inboxes import get_secrets

API_BASE = "https://api.twilio.com"


def compute_twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's request-signing scheme: the exact URL Twilio called, followed by
    every POST param as ``name + value`` sorted alphabetically by name, HMAC-SHA1
    keyed by the auth token, base64-encoded."""
    payload = url + "".join(name + value for name, value in sorted(params.items()))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _error_detail(status_code: int, data: object) -> str:
    if isinstance(data, dict) and isinstance(data.get("message"), str) and data["message"]:
        return data["message"]
    return f"HTTP {status_code}"


@register_sender("sms")
async def send(session: AsyncSession, inbox: Inbox, message: Message) -> None:
    conversation = await session.get(Conversation, message.conversation_id)
    contact_inbox = (
        await session.get(ContactInbox, conversation.contact_inbox_id)
        if conversation is not None and conversation.contact_inbox_id is not None
        else None
    )
    to_number = contact_inbox.source_id if contact_inbox is not None else None
    if not to_number:
        raise RuntimeError("conversation is not linked to an sms contact")

    secrets = get_secrets(inbox)
    account_sid = secrets.get("account_sid")
    auth_token = secrets.get("auth_token")
    if not account_sid or not auth_token:
        raise RuntimeError("twilio account_sid/auth_token are not configured")
    from_number = inbox.config.get("phone_number")
    if not from_number:
        raise RuntimeError("twilio phone_number is not configured")

    status_callback = f"{get_settings().public_base_url}/api/channels/sms/status/{inbox.id}"
    async with http_client() as client:
        response = await client.post(
            f"{API_BASE}/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={
                "To": to_number,
                "From": from_number,
                "Body": message.content,
                "StatusCallback": status_callback,
            },
        )
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.status_code != 201:
        raise RuntimeError(f"twilio send failed: {_error_detail(response.status_code, data)}")
    if isinstance(data, dict) and data.get("sid"):
        message.source_id = str(data["sid"])
