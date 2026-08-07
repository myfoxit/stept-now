"""Email channel adapter: outbound send + shared inbound processing.

Outbound replies go out via :func:`app.channels.email_transports.send_via_inbox`
using the inbox's configured transport, always with a generated ``Message-ID``
(``<uuid7@address-domain>``, stored in ``message.meta["message_id"]`` and, when
free, ``message.source_id`` so customer replies thread back via In-Reply-To), a
``reply+{conversation_id}@`` Reply-To, and In-Reply-To/References built from the
conversation's last inbound reference plus our last outbound id.

Inbound processing (:func:`process_inbound`) is shared by the webhook endpoints
in ``app.api.channels.email`` and the IMAP poller in
``app.channels.email_sync``: route by ``reply+{id}`` recipient, then
``In-Reply-To`` against known message ids, else land on the given (or address-
matched) email inbox. Quoted reply tails are stripped.
"""

from __future__ import annotations

import email as email_lib
import email.policy
import hmac
import html as html_lib
import re
from email.utils import getaddresses, parseaddr
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.email_transports import send_via_inbox
from app.channels.registry import register_sender
from app.core.db import uuid7
from app.core.errors import BlockedContactError, NotFoundError
from app.core.events import Actor
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import ChannelType, ContactInbox, Inbox
from app.models.message import Message, MessageDirection
from app.services import contacts as contacts_service
from app.services import conversations as conversations_service
from app.services.email import _to_text

# Quoted-reply tail heuristics (naive but covers the common clients).
_ORIGINAL_RE = re.compile(r"\n-{2,}\s*Original Message", re.IGNORECASE)
_ON_WROTE_RE = re.compile(r"\n>?\s*On .*?wrote:.*", re.DOTALL)
_REPLY_ADDRESS_RE = re.compile(r"reply\+([^@]+)@", re.IGNORECASE)

FALLBACK_MESSAGE_ID_DOMAIN = "stept.local"


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


def generate_message_id(inbox: Inbox) -> str:
    """``<uuid7@domain>`` — domain from the inbox address (fallback constant)."""
    address = inbox.config.get("address") or inbox.config.get("forward_to") or ""
    domain = address.split("@", 1)[1] if "@" in address else FALLBACK_MESSAGE_ID_DOMAIN
    return f"<{uuid7()}@{domain}>"


def _html_body(content: str) -> str:
    return "<p>" + html_lib.escape(content).replace("\n", "<br>\n") + "</p>"


# ---------------------------------------------------------------------------
# inbound shape + parsing
# ---------------------------------------------------------------------------


class InboundEmail(BaseModel):
    """The one internal shape every inbound source (generic webhook, ESP
    webhooks, IMAP poll) parses into."""

    model_config = ConfigDict(populate_by_name=True)

    to: str | list[str]
    from_: str | None = Field(default=None, alias="from")
    from_email: str | None = None
    subject: str | None = None
    text: str = ""
    html: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None


def parse_mime_email(raw: bytes) -> InboundEmail:
    """Parse a raw RFC822 message (IMAP fetch, SES raw content) into the
    internal shape. Prefers text/plain parts; falls back to html→text."""
    parsed = email_lib.message_from_bytes(raw, policy=email.policy.default)
    text = ""
    html_body: str | None = None
    if parsed.is_multipart():
        for part in parsed.walk():
            if part.get_content_maintype() != "text" or part.get_content_disposition():
                continue
            payload = part.get_content()
            if not isinstance(payload, str):
                continue
            if part.get_content_subtype() == "plain" and not text:
                text = payload
            elif part.get_content_subtype() == "html" and html_body is None:
                html_body = payload
    else:
        payload = parsed.get_content() if parsed.get_content_maintype() == "text" else ""
        if isinstance(payload, str):
            if parsed.get_content_subtype() == "html":
                html_body = payload
            else:
                text = payload
    recipients = parsed.get_all("To") or []
    recipients += parsed.get_all("Cc") or []
    return InboundEmail.model_validate(
        {
            "to": [str(value) for value in recipients],
            "from": str(parsed.get("From") or ""),
            "subject": str(parsed.get("Subject") or "") or None,
            "text": text,
            "html": html_body,
            "message_id": str(parsed.get("Message-ID") or "") or None,
            "in_reply_to": str(parsed.get("In-Reply-To") or "") or None,
        }
    )


# ---------------------------------------------------------------------------
# inbound routing + ingestion (shared by webhooks and the IMAP poller)
# ---------------------------------------------------------------------------


def _recipients(value: str | list[str]) -> list[str]:
    raw = value if isinstance(value, list) else [value]
    return [addr for _name, addr in getaddresses(raw) if addr]


async def _route_to_conversation(
    session: AsyncSession,
    recipients: list[str],
    in_reply_to: str | None,
    *,
    workspace_id: str | None = None,
) -> Conversation | None:
    """reply+{id} recipient first, then In-Reply-To matched against known
    message ids. ``workspace_id`` (set for inbox-bound endpoints) rejects
    cross-tenant matches — the path token only authenticates one inbox."""
    for address in recipients:
        conversation_id = reply_conversation_id(address)
        if conversation_id:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is not None and (
                workspace_id is None or conversation.workspace_id == workspace_id
            ):
                return conversation
    if in_reply_to:
        query = (
            select(Message)
            .where(Message.source_id == in_reply_to)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
        if workspace_id is not None:
            query = query.where(Message.workspace_id == workspace_id)
        message = (await session.execute(query)).scalars().first()
        if message is not None:
            return await session.get(Conversation, message.conversation_id)
    return None


async def _match_email_inbox(session: AsyncSession, recipients: list[str]) -> Inbox | None:
    """Legacy bare-endpoint routing: recipient equals a configured address or
    auto-generated forward-to address of an enabled email inbox."""
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
        for key in ("address", "forward_to"):
            candidate = inbox.config.get(key)
            if isinstance(candidate, str) and candidate.lower() in lowered:
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
    if contact.blocked:
        raise BlockedContactError("This contact is blocked")
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


async def process_inbound(
    session: AsyncSession,
    inbound: InboundEmail,
    *,
    inbox: Inbox | None = None,
    legacy_token: str | None = None,
) -> dict[str, Any]:
    """Route one inbound email into a conversation.

    ``inbox`` set (token-authenticated endpoints, IMAP poll): new conversations
    land there and thread matches are constrained to its workspace. ``inbox``
    None (legacy bare webhook): resolve by recipients; the resolved inbox's
    ``webhook_token`` — when it has one — must match ``legacy_token`` (404 on
    mismatch, and 404 when nothing resolves: no open relay)."""
    recipients = _recipients(inbound.to)
    sender = inbound.from_ or inbound.from_email or ""
    from_name, from_email = parseaddr(sender)
    if not from_email:
        return {"status": "ignored", "reason": "missing sender"}

    text = inbound.text or ""
    if not text.strip() and inbound.html:
        text = _to_text(inbound.html)
    content = strip_quoted(text)
    meta = {"message_id": inbound.message_id, "from": from_email}

    conversation = await _route_to_conversation(
        session,
        recipients,
        inbound.in_reply_to,
        workspace_id=inbox.workspace_id if inbox is not None else None,
    )
    if conversation is not None:
        if inbox is None:
            await _enforce_legacy_token(session, conversation.inbox_id, legacy_token)
        await _append_inbound(
            session,
            conversation,
            from_email=from_email,
            from_name=from_name,
            content=content,
            message_id=inbound.message_id,
            meta=meta,
        )
        return {"status": "appended", "conversation_id": conversation.id}

    target = inbox
    if target is None:
        target = await _match_email_inbox(session, recipients)
        if target is None:
            raise NotFoundError("No email inbox matches the recipients")
        _check_webhook_token(target, legacy_token)
    conversation, _message = await conversations_service.ingest_inbound(
        session,
        target,
        source_id=from_email,
        content=content,
        contact_info={"email": from_email, "name": from_name},
        message_source_id=inbound.message_id,
        subject=inbound.subject or None,
        meta=meta,
    )
    return {"status": "created", "conversation_id": conversation.id}


def _check_webhook_token(inbox: Inbox, token: str | None) -> None:
    expected = inbox.config.get("webhook_token")
    if expected and not hmac.compare_digest(str(expected), token or ""):
        raise NotFoundError("Email inbox not found")


async def _enforce_legacy_token(session: AsyncSession, inbox_id: str, token: str | None) -> None:
    inbox = await session.get(Inbox, inbox_id)
    if inbox is not None and inbox.channel_type == ChannelType.EMAIL:
        _check_webhook_token(inbox, token)


# ---------------------------------------------------------------------------
# outbound
# ---------------------------------------------------------------------------


async def _thread_headers(
    session: AsyncSession, conversation_id: str, current_message_id: str
) -> tuple[str | None, list[str]]:
    """(In-Reply-To, References) from the conversation history: the last
    inbound reference plus our last outbound generated id, oldest first."""

    async def _last(direction: MessageDirection) -> Message | None:
        return (
            (
                await session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.direction == direction,
                        Message.id != current_message_id,
                    )
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

    def _ref(message: Message | None) -> str | None:
        if message is None:
            return None
        reference = message.meta.get("message_id") if isinstance(message.meta, dict) else None
        return reference or message.source_id

    last_in = await _last(MessageDirection.IN)
    last_out = await _last(MessageDirection.OUT)
    ordered = sorted(
        (m for m in (last_in, last_out) if m is not None),
        key=lambda m: (m.created_at, m.id),
    )
    references = [ref for ref in (_ref(m) for m in ordered) if ref]
    return _ref(last_in), references


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

    message_id = generate_message_id(inbox)
    headers: dict[str, str] = {"Message-ID": message_id}
    in_reply_to, references = await _thread_headers(session, conversation.id, message.id)
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
    if references:
        headers["References"] = " ".join(references)

    provider_message_id = await send_via_inbox(
        session,
        inbox,
        to=to_address,
        subject=subject,
        html=_html_body(message.content),
        text=message.content,
        reply_to=reply_to,
        headers=headers,
    )

    meta = dict(message.meta or {})
    meta["message_id"] = message_id
    if provider_message_id:
        meta["provider_message_id"] = provider_message_id
    message.meta = meta
    if not message.source_id:
        # Lets a customer reply's In-Reply-To route straight back to this thread.
        message.source_id = message_id
