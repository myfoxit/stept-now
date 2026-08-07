"""Inbound email webhooks: token-secured generic endpoint + per-ESP parsers.

Every email inbox gets an auto-generated ``webhook_token``
(``services.inboxes``); inbound URLs embed it:

- ``POST /inbound/{inbox_id}/{token}`` — generic JSON (the internal shape).
- ``POST /inbound/{resend|postmark|sendgrid|mailgun|ses}/{inbox_id}/{token}`` —
  provider payloads parsed into the same :class:`InboundEmail` shape. Mailgun
  and Resend webhook signatures are verified when their signing secrets are
  configured; the SES route speaks SNS (SubscriptionConfirmation handshake is
  SSRF-guarded via ``app.rag.connectors.check_public_url``).

Token mismatches 404 without confirming the inbox exists. The old bare
``/inbound`` stays for one release: it resolves the inbox by routing as before
but requires ``?token=`` once the resolved inbox has a ``webhook_token``, and
404s when nothing resolves (no open relay). Importing this module also
registers the outbound email sender and the IMAP poll task/scheduler job.
"""

from __future__ import annotations

import base64
import email.policy
import hashlib
import hmac
import json
from email.parser import Parser
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession

import app.channels.email_sync  # noqa: F401 — registers the IMAP poll task + scheduler job
from app.channels.email import (  # noqa: F401 — InboundEmail re-exported for compat
    InboundEmail,
    parse_mime_email,
    process_inbound,
)
from app.core.deps import Db
from app.core.errors import BadRequestError, NotFoundError, UnauthorizedError
from app.models.inbox import ChannelType, Inbox
from app.rag.connectors import check_public_url
from app.rag.tasks import FetchError
from app.services.inboxes import get_secrets

router = APIRouter()

SNS_CONFIRM_TIMEOUT = 10.0


async def _inbox_by_token(session: AsyncSession, inbox_id: str, token: str) -> Inbox:
    """404 on any mismatch — never confirm an inbox id without its token."""
    inbox = await session.get(Inbox, inbox_id)
    if inbox is None or inbox.channel_type != ChannelType.EMAIL or not inbox.enabled:
        raise NotFoundError("Email inbox not found")
    expected = inbox.config.get("webhook_token")
    if not expected or not hmac.compare_digest(str(expected), token):
        raise NotFoundError("Email inbox not found")
    return inbox


# ---------------------------------------------------------------------------
# signature verification
# ---------------------------------------------------------------------------


def verify_mailgun_signature(signing_key: str, timestamp: str, token: str, signature: str) -> bool:
    expected = hmac.new(
        signing_key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_svix_signature(
    secret: str, message_id: str, timestamp: str, body: bytes, signature_header: str
) -> bool:
    """Svix-style verification (Resend webhooks): base64 HMAC-SHA256 over
    ``{id}.{timestamp}.{body}`` keyed by the (base64) endpoint secret."""
    key_b64 = secret.removeprefix("whsec_")
    try:
        key = base64.b64decode(key_b64 + "=" * (-len(key_b64) % 4))
    except ValueError:
        return False
    signed = f"{message_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for candidate in signature_header.split():
        version, _, signature = candidate.partition(",")
        if version == "v1" and signature and hmac.compare_digest(signature, expected):
            return True
    return False


# ---------------------------------------------------------------------------
# per-ESP payload parsing → InboundEmail
# ---------------------------------------------------------------------------


def _header_lookup(headers: Any, name: str) -> str | None:
    """Provider header collections: list of {Name,Value}/{name,value} or dict."""
    wanted = name.lower()
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == wanted and value:
                return str(value)
        return None
    if isinstance(headers, list):
        for item in headers:
            if not isinstance(item, dict):
                continue
            key = item.get("Name") or item.get("name")
            value = item.get("Value") or item.get("value")
            if key and str(key).lower() == wanted and value:
                return str(value)
    return None


def _parse_resend(payload: dict[str, Any]) -> InboundEmail | None:
    event_type = payload.get("type")
    if event_type and not str(event_type).endswith("email.received"):
        return None
    data = payload.get("data") or {}
    sender = data.get("from")
    if isinstance(sender, dict):
        name, addr = sender.get("name") or "", sender.get("email") or ""
        sender = f"{name} <{addr}>" if name else addr
    headers = data.get("headers")
    return InboundEmail.model_validate(
        {
            "to": data.get("to") or [],
            "from": sender,
            "subject": data.get("subject"),
            "text": data.get("text") or "",
            "html": data.get("html"),
            "message_id": data.get("message_id") or _header_lookup(headers, "Message-ID"),
            "in_reply_to": data.get("in_reply_to") or _header_lookup(headers, "In-Reply-To"),
        }
    )


def _parse_postmark(payload: dict[str, Any]) -> InboundEmail:
    from_full = payload.get("FromFull") or {}
    sender = from_full.get("Email") or payload.get("From") or ""
    if from_full.get("Name") and from_full.get("Email"):
        sender = f"{from_full['Name']} <{from_full['Email']}>"
    to_full = payload.get("ToFull") or []
    to = [item["Email"] for item in to_full if isinstance(item, dict) and item.get("Email")]
    headers = payload.get("Headers") or []
    return InboundEmail.model_validate(
        {
            "to": to or [str(payload.get("To") or "")],
            "from": sender,
            "subject": payload.get("Subject"),
            "text": payload.get("TextBody") or "",
            "html": payload.get("HtmlBody"),
            "message_id": _header_lookup(headers, "Message-ID") or payload.get("MessageID"),
            "in_reply_to": _header_lookup(headers, "In-Reply-To"),
        }
    )


def _parse_sendgrid(form: dict[str, str]) -> InboundEmail:
    message_id: str | None = None
    in_reply_to: str | None = None
    headers_text = form.get("headers") or ""
    if headers_text:
        parsed = Parser(policy=email.policy.default).parsestr(headers_text)
        message_id = str(parsed.get("Message-ID") or "") or None
        in_reply_to = str(parsed.get("In-Reply-To") or "") or None
    return InboundEmail.model_validate(
        {
            "to": form.get("to") or "",
            "from": form.get("from"),
            "subject": form.get("subject"),
            "text": form.get("text") or "",
            "html": form.get("html"),
            "message_id": message_id,
            "in_reply_to": in_reply_to,
        }
    )


def _parse_mailgun(form: dict[str, str]) -> InboundEmail:
    return InboundEmail.model_validate(
        {
            "to": form.get("recipient") or form.get("To") or "",
            "from": form.get("from") or form.get("sender"),
            "subject": form.get("subject"),
            "text": form.get("stripped-text") or form.get("body-plain") or "",
            "html": form.get("body-html"),
            "message_id": form.get("Message-Id") or form.get("Message-ID"),
            "in_reply_to": form.get("In-Reply-To"),
        }
    )


async def _form_fields(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: value for key, value in form.multi_items() if isinstance(value, str)}


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.post("/inbound")
async def inbound_email(body: InboundEmail, session: Db, token: str = "") -> dict[str, Any]:
    """Legacy bare endpoint (one release of back-compat). Routing resolves the
    inbox; once that inbox carries a webhook_token the ``?token=`` query param
    must match, and unresolvable mail 404s instead of being accepted."""
    return await process_inbound(session, body, legacy_token=token or None)


@router.post("/inbound/{inbox_id}/{token}")
async def inbound_email_tokened(
    inbox_id: str, token: str, body: InboundEmail, session: Db
) -> dict[str, Any]:
    inbox = await _inbox_by_token(session, inbox_id, token)
    return await process_inbound(session, body, inbox=inbox)


@router.post("/inbound/resend/{inbox_id}/{token}")
async def inbound_resend(
    inbox_id: str, token: str, request: Request, session: Db
) -> dict[str, Any]:
    inbox = await _inbox_by_token(session, inbox_id, token)
    raw = await request.body()
    secret = get_secrets(inbox).get("resend_webhook_secret")
    if secret:
        valid = verify_svix_signature(
            str(secret),
            request.headers.get("svix-id") or "",
            request.headers.get("svix-timestamp") or "",
            raw,
            request.headers.get("svix-signature") or "",
        )
        if not valid:
            raise UnauthorizedError("Invalid Resend webhook signature")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except ValueError as exc:
        raise BadRequestError("Invalid Resend payload") from exc
    inbound = _parse_resend(payload)
    if inbound is None:
        return {"status": "ignored", "reason": "unsupported event type"}
    return await process_inbound(session, inbound, inbox=inbox)


@router.post("/inbound/postmark/{inbox_id}/{token}")
async def inbound_postmark(
    inbox_id: str, token: str, payload: dict[str, Any], session: Db
) -> dict[str, Any]:
    inbox = await _inbox_by_token(session, inbox_id, token)
    return await process_inbound(session, _parse_postmark(payload), inbox=inbox)


@router.post("/inbound/sendgrid/{inbox_id}/{token}")
async def inbound_sendgrid(
    inbox_id: str, token: str, request: Request, session: Db
) -> dict[str, Any]:
    inbox = await _inbox_by_token(session, inbox_id, token)
    fields = await _form_fields(request)
    return await process_inbound(session, _parse_sendgrid(fields), inbox=inbox)


@router.post("/inbound/mailgun/{inbox_id}/{token}")
async def inbound_mailgun(
    inbox_id: str, token: str, request: Request, session: Db
) -> dict[str, Any]:
    inbox = await _inbox_by_token(session, inbox_id, token)
    fields = await _form_fields(request)
    signing_key = get_secrets(inbox).get("mailgun_signing_key")
    if signing_key:
        valid = verify_mailgun_signature(
            str(signing_key),
            fields.get("timestamp") or "",
            fields.get("token") or "",
            fields.get("signature") or "",
        )
        if not valid:
            raise UnauthorizedError("Invalid Mailgun signature")
    return await process_inbound(session, _parse_mailgun(fields), inbox=inbox)


@router.post("/inbound/ses/{inbox_id}/{token}")
async def inbound_ses(inbox_id: str, token: str, request: Request, session: Db) -> dict[str, Any]:
    """SNS envelope: confirm subscriptions (SSRF-guarded GET), then unwrap SES
    receipt notifications carrying the raw MIME in ``content``. Receipts using
    the S3 action (no inline content) are acknowledged but ignored."""
    inbox = await _inbox_by_token(session, inbox_id, token)
    raw = await request.body()
    try:
        envelope = json.loads(raw.decode("utf-8") or "{}")
    except ValueError as exc:
        raise BadRequestError("Invalid SNS payload") from exc
    message_type = request.headers.get("x-amz-sns-message-type") or envelope.get("Type")

    if message_type == "SubscriptionConfirmation":
        url = str(envelope.get("SubscribeURL") or "")
        hostname = urlsplit(url).hostname or ""
        if not url.startswith("https://") or not hostname.endswith(".amazonaws.com"):
            raise BadRequestError("SubscribeURL must be an https amazonaws.com URL")
        try:
            check_public_url(url)
        except FetchError as exc:
            raise BadRequestError(str(exc)) from exc
        async with httpx.AsyncClient(timeout=SNS_CONFIRM_TIMEOUT) as http:
            response = await http.get(url)
        if response.status_code >= 300:
            raise BadRequestError(f"SNS confirmation failed ({response.status_code})")
        return {"status": "confirmed"}
    if message_type == "UnsubscribeConfirmation":
        return {"status": "ignored", "reason": "unsubscribe confirmation"}

    try:
        receipt = json.loads(str(envelope.get("Message") or "{}"))
    except ValueError as exc:
        raise BadRequestError("Invalid SES notification message") from exc
    content = receipt.get("content")
    if not content:
        return {"status": "ignored", "reason": "no inline content (S3 receipt action)"}
    try:
        raw_mime = base64.b64decode(content)
    except ValueError as exc:
        raise BadRequestError("Invalid SES raw content") from exc
    inbound = parse_mime_email(raw_mime)
    if not inbound.message_id:
        inbound.message_id = (receipt.get("mail") or {}).get("messageId")
    return await process_inbound(session, inbound, inbox=inbox)
