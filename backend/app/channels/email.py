"""Email channel adapter: outbound SMTP sender + inbound reply parsing helpers.

Outbound replies go out via ``app.services.email.send_email`` from the inbox's
configured address, with a ``reply+{conversation_id}@`` Reply-To so the inbound
webhook can route the customer's reply straight back to the thread. In-Reply-To/
References are threaded off the last inbound message when available.
"""

from __future__ import annotations

import html as html_lib
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.registry import register_sender
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.message import Message, MessageDirection
from app.services.email import send_email

# Quoted-reply tail heuristics (naive but covers the common clients).
_ORIGINAL_RE = re.compile(r"\n-{2,}\s*Original Message", re.IGNORECASE)
_ON_WROTE_RE = re.compile(r"\n>?\s*On .*?wrote:.*", re.DOTALL)
_REPLY_ADDRESS_RE = re.compile(r"reply\+([^@]+)@", re.IGNORECASE)


def reply_conversation_id(address: str) -> str | None:
    """Extract the conversation id from a ``reply+{id}@domain`` address."""
    match = _REPLY_ADDRESS_RE.search(address)
    return match.group(1) if match else None


def reply_address(address: str, conversation_id: str) -> str:
    domain = address.split("@", 1)[1] if "@" in address else address
    return f"reply+{conversation_id}@{domain}"


def strip_quoted(text: str) -> str:
    """Drop quoted reply tails: '-----Original', 'On … wrote:' blocks, and any
    trailing ``>``-quoted lines."""
    text = _ORIGINAL_RE.split(text, maxsplit=1)[0]
    text = _ON_WROTE_RE.split(text, maxsplit=1)[0]
    lines = text.split("\n")
    while lines and lines[-1].lstrip().startswith(">"):
        lines.pop()
    return "\n".join(lines).strip()


def _html_body(content: str) -> str:
    return "<p>" + html_lib.escape(content).replace("\n", "<br>\n") + "</p>"


async def _last_inbound_reference(session: AsyncSession, conversation_id: str) -> str | None:
    message = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.direction == MessageDirection.IN,
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if message is None:
        return None
    reference = message.meta.get("message_id") if isinstance(message.meta, dict) else None
    return reference or message.source_id


@register_sender("email")
async def send(session: AsyncSession, inbox: Inbox, message: Message) -> None:
    conversation = await session.get(Conversation, message.conversation_id)
    if conversation is None:
        raise RuntimeError("conversation not found")
    contact = await session.get(Contact, conversation.contact_id)
    to_address = contact.email if contact is not None else None
    if not to_address:
        raise RuntimeError("contact has no email address")

    address = inbox.config.get("address")
    reply_to = reply_address(address, conversation.id) if address else None
    subject = f"Re: {conversation.subject or 'your conversation'}"

    headers: dict[str, str] = {}
    reference = await _last_inbound_reference(session, conversation.id)
    if reference:
        headers["In-Reply-To"] = reference
        headers["References"] = reference

    ok = await send_email(
        to_address,
        subject,
        _html_body(message.content),
        reply_to=reply_to,
        from_override=address,
        headers=headers,
    )
    if not ok:
        raise RuntimeError("email delivery failed")
