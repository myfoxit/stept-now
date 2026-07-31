"""Inbound email webhook: parse a provider POST into an inbound message.

Routing precedence: an explicit ``reply+{conversation_id}@`` recipient, then an
``in_reply_to`` header matched to a known message, else a new conversation on
the email inbox whose configured address is a recipient. Importing this module
also registers the outbound email sender.
"""

from __future__ import annotations

from email.utils import getaddresses, parseaddr
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.channels.email  # noqa: F401 — registers the outbound "email" sender
from app.channels.email import reply_conversation_id, strip_quoted
from app.core.deps import Db
from app.core.events import Actor
from app.models.conversation import Conversation
from app.models.inbox import ChannelType, ContactInbox, Inbox
from app.models.message import Message, MessageDirection
from app.services import contacts as contacts_service
from app.services import conversations as conversations_service

router = APIRouter()


class InboundEmail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    to: str | list[str]
    from_: str | None = Field(default=None, alias="from")
    from_email: str | None = None
    subject: str | None = None
    text: str = ""
    html: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None


def _recipients(value: str | list[str]) -> list[str]:
    raw = value if isinstance(value, list) else [value]
    return [addr for _name, addr in getaddresses(raw) if addr]


async def _route_to_conversation(
    session: AsyncSession, recipients: list[str], in_reply_to: str | None
) -> Conversation | None:
    for address in recipients:
        conversation_id = reply_conversation_id(address)
        if conversation_id:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is not None:
                return conversation
    if in_reply_to:
        message = (
            (
                await session.execute(
                    select(Message)
                    .where(Message.source_id == in_reply_to)
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if message is not None:
            return await session.get(Conversation, message.conversation_id)
    return None


async def _match_email_inbox(session: AsyncSession, recipients: list[str]) -> Inbox | None:
    lowered = {address.lower() for address in recipients}
    inboxes = (
        (
            await session.execute(
                select(Inbox).where(
                    Inbox.channel_type == ChannelType.EMAIL, Inbox.enabled.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    for inbox in inboxes:
        address = inbox.config.get("address")
        if isinstance(address, str) and address.lower() in lowered:
            return inbox
    return None


async def _append_inbound(
    session: AsyncSession,
    conversation: Conversation,
    *,
    from_email: str,
    from_name: str,
    content: str,
    message_id: str | None,
    meta: dict[str, Any],
) -> Message:
    workspace_id = conversation.workspace_id
    contact, _created = await contacts_service.find_or_create(
        session, workspace_id, email=from_email, name=from_name
    )
    contact_inbox = (
        await session.execute(
            select(ContactInbox).where(
                ContactInbox.inbox_id == conversation.inbox_id,
                ContactInbox.source_id == from_email,
            )
        )
    ).scalar_one_or_none()
    if contact_inbox is None:
        session.add(
            ContactInbox(
                workspace_id=workspace_id,
                contact_id=contact.id,
                inbox_id=conversation.inbox_id,
                source_id=from_email,
            )
        )
        await session.flush()
    actor = Actor(type="contact", id=contact.id, label=contact.name or None)
    return await conversations_service.add_message(
        session,
        conversation,
        direction=MessageDirection.IN.value,
        author_type="contact",
        author_id=contact.id,
        author_name=contact.name or from_email,
        content=content,
        source_id=message_id,
        meta=meta,
        actor=actor,
        deliver=False,
    )


@router.post("/inbound")
async def inbound_email(body: InboundEmail, session: Db) -> dict[str, Any]:
    recipients = _recipients(body.to)
    sender = body.from_ or body.from_email or ""
    from_name, from_email = parseaddr(sender)
    if not from_email:
        return {"status": "ignored", "reason": "missing sender"}

    content = strip_quoted(body.text or "")
    meta = {"message_id": body.message_id, "from": from_email}

    conversation = await _route_to_conversation(session, recipients, body.in_reply_to)
    if conversation is not None:
        await _append_inbound(
            session,
            conversation,
            from_email=from_email,
            from_name=from_name,
            content=content,
            message_id=body.message_id,
            meta=meta,
        )
        return {"status": "appended", "conversation_id": conversation.id}

    inbox = await _match_email_inbox(session, recipients)
    if inbox is None:
        return {"status": "ignored", "reason": "no matching inbox"}
    conversation, _message = await conversations_service.ingest_inbound(
        session,
        inbox,
        source_id=from_email,
        content=content,
        contact_info={"email": from_email, "name": from_name},
        message_source_id=body.message_id,
        subject=body.subject or None,
        meta=meta,
    )
    return {"status": "created", "conversation_id": conversation.id}
