"""Per-inbox outbound email transports (W11).

`send_via_inbox` dispatches on ``Inbox.config["transport"]``:

- ``global``  — the instance-wide SMTP/console path (`app.services.email`).
- ``smtp``    — per-inbox SMTP with the inbox's host/port/security/credentials.
- ``gmail`` / ``microsoft`` — SMTP XOAUTH2 with a live token from the
  integrations tokens seam (`app.integrations.tokens`).
- ``ses`` / ``resend`` / ``postmark`` / ``sendgrid`` / ``mailgun`` — provider
  HTTP APIs (SESv2 signed with a local stdlib SigV4 signer — no boto).

All failures raise :class:`EmailDeliveryError`; the channel registry turns the
message into a failed delivery with the error text. Provider message ids are
returned when the API reports one. API-based ESPs may rewrite the MIME
Message-ID we pass in ``headers`` (Postmark always does); threading still works
because References/In-Reply-To pass through untouched.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlsplit

import aiosmtplib
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import log
from app.integrations import tokens as integration_tokens
from app.models.inbox import Inbox
from app.services.email import _to_text, send_email
from app.services.inboxes import get_secrets

try:  # BE-A fills app.integrations.oauth; fall back until it lands.
    from app.integrations.oauth import IntegrationAuthError  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover — exercised only before BE-A lands

    class IntegrationAuthError(Exception):  # type: ignore[no-redef]
        """Placeholder matching the contract in docs/INTEGRATIONS-CONTRACTS.md."""


logger = log("email_transports")

HTTP_TIMEOUT = 20.0

RESEND_SEND_URL = "https://api.resend.com/emails"
POSTMARK_SEND_URL = "https://api.postmarkapp.com/email"
SENDGRID_SEND_URL = "https://api.sendgrid.com/v3/mail/send"

# transport → (SMTP host, IMAP host) auto-configured by app.services.inboxes.
OAUTH_SMTP_HOSTS = {"gmail": "smtp.gmail.com", "microsoft": "smtp.office365.com"}
OAUTH_IMAP_HOSTS = {"gmail": "imap.gmail.com", "microsoft": "outlook.office365.com"}

EMAIL_TRANSPORTS = (
    "global",
    "smtp",
    "gmail",
    "microsoft",
    "ses",
    "resend",
    "postmark",
    "sendgrid",
    "mailgun",
)


class EmailDeliveryError(RuntimeError):
    """Outbound email could not be handed to the transport."""


def ses_endpoint(region: str) -> str:
    return f"https://email.{region}.amazonaws.com/v2/email/outbound-emails"


def mailgun_endpoint(domain: str, base: str) -> str:
    host = "api.eu.mailgun.net" if base == "eu" else "api.mailgun.net"
    return f"https://{host}/v3/{domain}/messages"


def xoauth2_string(login: str, token: str) -> str:
    """Base64 SASL XOAUTH2 initial response (RFC layout per Google/Microsoft)."""
    return base64.b64encode(f"user={login}\x01auth=Bearer {token}\x01\x01".encode()).decode()


def _sigv4_headers(
    *,
    method: str,
    url: str,
    region: str,
    service: str,
    access_key_id: str,
    secret_access_key: str,
    body: bytes,
    now: datetime | None = None,
) -> dict[str, str]:
    """Minimal AWS SigV4 for a JSON POST without query params (stdlib only).

    Signs ``content-type;host;x-amz-date`` over the SHA-256 payload hash —
    exactly the subset SESv2 ``SendEmail`` needs.
    """
    parts = urlsplit(url)
    host = parts.netloc
    path = parts.path or "/"
    now = now or datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    content_type = "application/json"

    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_headers = f"content-type:{content_type}\nhost:{host}\nx-amz-date:{amz_date}\n"
    signed_headers = "content-type;host;x-amz-date"
    canonical_request = "\n".join(
        [method, path, "", canonical_headers, signed_headers, payload_hash]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = _hmac(("AWS4" + secret_access_key).encode(), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    k_signing = _hmac(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {"Authorization": authorization, "X-Amz-Date": amz_date, "Content-Type": content_type}


def _build_mime(
    *,
    from_addr: str,
    to: str,
    subject: str,
    html: str,
    text: str,
    reply_to: str | None,
    headers: dict[str, str] | None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    for key, value in (headers or {}).items():
        message[key] = value
    message.set_content(text or _to_text(html))
    message.add_alternative(html, subtype="html")
    return message


def _http_error(provider: str, response: httpx.Response) -> EmailDeliveryError:
    detail = response.text[:200]
    return EmailDeliveryError(f"{provider} send failed ({response.status_code}): {detail}")


def _require_secret(secrets: dict[str, Any], key: str, provider: str) -> str:
    value = secrets.get(key)
    if not value:
        raise EmailDeliveryError(f"{provider} transport is missing the {key} secret")
    return str(value)


# ---------------------------------------------------------------------------
# transports
# ---------------------------------------------------------------------------

# Seams for tests (and future pooling): module-level so they can be patched.
_smtp_send = aiosmtplib.send


def _smtp_client(host: str, port: int) -> aiosmtplib.SMTP:
    return aiosmtplib.SMTP(hostname=host, port=port, start_tls=True, timeout=HTTP_TIMEOUT)


async def _send_global(
    *,
    to: str,
    subject: str,
    html: str,
    reply_to: str | None,
    from_addr: str | None,
    headers: dict[str, str] | None,
) -> str | None:
    ok = await send_email(
        to, subject, html, reply_to=reply_to, from_override=from_addr, headers=headers
    )
    if not ok:
        raise EmailDeliveryError("email delivery failed")
    return None


async def _send_smtp(inbox: Inbox, message: EmailMessage, to: str) -> str | None:
    smtp_cfg = inbox.config.get("smtp") or {}
    host = smtp_cfg.get("host")
    if not host:
        raise EmailDeliveryError("smtp transport is missing smtp.host")
    port = int(smtp_cfg.get("port") or 587)
    security = smtp_cfg.get("security") or "starttls"
    username = smtp_cfg.get("username") or None
    password = get_secrets(inbox).get("smtp_password") or None
    try:
        await _smtp_send(
            message,
            recipients=[to],
            hostname=host,
            port=port,
            username=username if password else None,
            password=password,
            use_tls=security == "tls",
            start_tls=True if security == "starttls" else None,
            timeout=HTTP_TIMEOUT,
        )
    except aiosmtplib.SMTPException as exc:
        raise EmailDeliveryError(f"smtp send failed: {exc}") from exc
    return None


async def _send_xoauth2(
    session: AsyncSession, inbox: Inbox, message: EmailMessage, to: str, transport: str
) -> str | None:
    provider_name = "Gmail" if transport == "gmail" else "Microsoft"
    connection_id = inbox.config.get("connection_id")
    if not connection_id:
        raise EmailDeliveryError(f"{transport} transport has no linked connection")
    try:
        connection = await integration_tokens.get_connection(  # type: ignore[attr-defined]
            session, inbox.workspace_id, connection_id
        )
        token = await integration_tokens.get_valid_access_token(  # type: ignore[attr-defined]
            session, connection
        )
    except IntegrationAuthError as exc:
        raise EmailDeliveryError(
            f"{provider_name} authorization expired — reauthorize "
            f"{provider_name} in Settings → Integrations"
        ) from exc
    smtp_cfg = inbox.config.get("smtp") or {}
    login = smtp_cfg.get("username") or connection.account_label
    if not login:
        raise EmailDeliveryError(f"{transport} transport has no SMTP login address")
    host = smtp_cfg.get("host") or OAUTH_SMTP_HOSTS[transport]
    port = int(smtp_cfg.get("port") or 587)

    client = _smtp_client(host, port)
    try:
        await client.connect()  # start_tls=True: connection comes up TLS-wrapped
        await client.ehlo()
        response = await client.execute_command(
            b"AUTH", b"XOAUTH2", xoauth2_string(login, token).encode()
        )
        if response.code == 334:  # server pushed a base64 error blob; complete the exchange
            response = await client.execute_command(b"")
        if response.code != 235:
            raise EmailDeliveryError(
                f"{provider_name} XOAUTH2 authentication failed — reauthorize "
                f"{provider_name} in Settings → Integrations"
            )
        await client.send_message(message, recipients=[to])
    except aiosmtplib.SMTPException as exc:
        raise EmailDeliveryError(f"{transport} smtp send failed: {exc}") from exc
    finally:
        try:
            await client.quit()
        except Exception:  # noqa: BLE001 — already disconnected is fine
            client.close()
    return None


async def _send_ses(inbox: Inbox, message: EmailMessage, to: str, from_addr: str) -> str | None:
    secrets = get_secrets(inbox)
    region = (inbox.config.get("ses") or {}).get("region")
    if not region:
        raise EmailDeliveryError("ses transport is missing ses.region")
    access_key = _require_secret(secrets, "ses_access_key_id", "ses")
    secret_key = _require_secret(secrets, "ses_secret_access_key", "ses")
    url = ses_endpoint(region)
    # Raw MIME so Message-ID/References survive verbatim.
    body = json.dumps(
        {
            "FromEmailAddress": from_addr,
            "Destination": {"ToAddresses": [to]},
            "Content": {"Raw": {"Data": base64.b64encode(message.as_bytes()).decode()}},
        }
    ).encode()
    headers = _sigv4_headers(
        method="POST",
        url=url,
        region=region,
        service="ses",
        access_key_id=access_key,
        secret_access_key=secret_key,
        body=body,
    )
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(url, content=body, headers=headers)
    if response.status_code >= 300:
        raise _http_error("ses", response)
    message_id = response.json().get("MessageId")
    return str(message_id) if message_id else None


async def _send_resend(
    inbox: Inbox,
    *,
    from_addr: str,
    to: str,
    subject: str,
    html: str,
    text: str,
    reply_to: str | None,
    headers: dict[str, str] | None,
) -> str | None:
    api_key = _require_secret(get_secrets(inbox), "resend_api_key", "resend")
    payload: dict[str, Any] = {
        "from": from_addr,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    if reply_to:
        payload["reply_to"] = reply_to
    if headers:
        payload["headers"] = headers
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(
            RESEND_SEND_URL, json=payload, headers={"Authorization": f"Bearer {api_key}"}
        )
    if response.status_code >= 300:
        raise _http_error("resend", response)
    message_id = response.json().get("id")
    return str(message_id) if message_id else None


async def _send_postmark(
    inbox: Inbox,
    *,
    from_addr: str,
    to: str,
    subject: str,
    html: str,
    text: str,
    reply_to: str | None,
    headers: dict[str, str] | None,
) -> str | None:
    token = _require_secret(get_secrets(inbox), "postmark_server_token", "postmark")
    payload: dict[str, Any] = {
        "From": from_addr,
        "To": to,
        "Subject": subject,
        "HtmlBody": html,
        "TextBody": text,
    }
    if reply_to:
        payload["ReplyTo"] = reply_to
    if headers:
        payload["Headers"] = [{"Name": key, "Value": value} for key, value in headers.items()]
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(
            POSTMARK_SEND_URL,
            json=payload,
            headers={"X-Postmark-Server-Token": token, "Accept": "application/json"},
        )
    if response.status_code >= 300:
        raise _http_error("postmark", response)
    message_id = response.json().get("MessageID")
    return str(message_id) if message_id else None


async def _send_sendgrid(
    inbox: Inbox,
    *,
    from_addr: str,
    to: str,
    subject: str,
    html: str,
    text: str,
    reply_to: str | None,
    headers: dict[str, str] | None,
) -> str | None:
    api_key = _require_secret(get_secrets(inbox), "sendgrid_api_key", "sendgrid")
    payload: dict[str, Any] = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": from_addr},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text},
            {"type": "text/html", "value": html},
        ],
    }
    if reply_to:
        payload["reply_to"] = {"email": reply_to}
    if headers:
        payload["headers"] = headers
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(
            SENDGRID_SEND_URL, json=payload, headers={"Authorization": f"Bearer {api_key}"}
        )
    if response.status_code >= 300:
        raise _http_error("sendgrid", response)
    return response.headers.get("X-Message-Id") or None


async def _send_mailgun(
    inbox: Inbox,
    *,
    from_addr: str,
    to: str,
    subject: str,
    html: str,
    text: str,
    reply_to: str | None,
    headers: dict[str, str] | None,
) -> str | None:
    mailgun_cfg = inbox.config.get("mailgun") or {}
    domain = mailgun_cfg.get("domain")
    if not domain:
        raise EmailDeliveryError("mailgun transport is missing mailgun.domain")
    api_key = _require_secret(get_secrets(inbox), "mailgun_api_key", "mailgun")
    data: dict[str, str] = {
        "from": from_addr,
        "to": to,
        "subject": subject,
        "text": text,
        "html": html,
    }
    if reply_to:
        data["h:Reply-To"] = reply_to
    for key, value in (headers or {}).items():
        data[f"h:{key}"] = value
    url = mailgun_endpoint(domain, mailgun_cfg.get("base") or "us")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(url, data=data, auth=("api", api_key))
    if response.status_code >= 300:
        raise _http_error("mailgun", response)
    message_id = response.json().get("id")
    return str(message_id) if message_id else None


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


async def send_via_inbox(
    session: AsyncSession,
    inbox: Inbox,
    *,
    to: str,
    subject: str,
    html: str,
    text: str,
    reply_to: str | None,
    headers: dict[str, str] | None,
) -> str | None:
    """Send one outbound email through the inbox's configured transport.

    Returns the provider message id when the API reports one (None for SMTP
    paths). Raises :class:`EmailDeliveryError` on any delivery problem.
    """
    transport = inbox.config.get("transport") or "global"
    from_addr = inbox.config.get("address") or get_settings().email_from

    if transport == "global":
        return await _send_global(
            to=to,
            subject=subject,
            html=html,
            reply_to=reply_to,
            from_addr=inbox.config.get("address"),
            headers=headers,
        )

    if transport in ("smtp", "gmail", "microsoft", "ses"):
        message = _build_mime(
            from_addr=from_addr,
            to=to,
            subject=subject,
            html=html,
            text=text,
            reply_to=reply_to,
            headers=headers,
        )
        if transport == "smtp":
            return await _send_smtp(inbox, message, to)
        if transport == "ses":
            return await _send_ses(inbox, message, to, from_addr)
        return await _send_xoauth2(session, inbox, message, to, transport)

    kwargs: dict[str, Any] = {
        "from_addr": from_addr,
        "to": to,
        "subject": subject,
        "html": html,
        "text": text,
        "reply_to": reply_to,
        "headers": headers,
    }
    if transport == "resend":
        return await _send_resend(inbox, **kwargs)
    if transport == "postmark":
        return await _send_postmark(inbox, **kwargs)
    if transport == "sendgrid":
        return await _send_sendgrid(inbox, **kwargs)
    if transport == "mailgun":
        return await _send_mailgun(inbox, **kwargs)
    raise EmailDeliveryError(f"unknown email transport: {transport}")
