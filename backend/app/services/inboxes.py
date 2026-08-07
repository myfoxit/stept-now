"""Inbox lifecycle: CRUD, encrypted channel secrets, widget embed snippet.

Every workspace gets a default widget inbox on creation (workspace.created
handler); other channels are added from settings (channels:manage).

Email inboxes (W11) carry a transport config validated here on create/update:

    config: {address, transport, connection_id, forward_to, webhook_token,
             smtp: {host, port, username, security}, ses: {region},
             mailgun: {domain, base},
             imap: {enabled, host, port, username, poll_minutes, mark_seen}}
    secrets: smtp_password, imap_password, ses_access_key_id,
             ses_secret_access_key, resend_api_key, resend_webhook_secret,
             postmark_server_token, sendgrid_api_key, mailgun_api_key,
             mailgun_signing_key

``webhook_token`` (inbound URL auth) and ``forward_to`` (when
``settings.inbound_email_domain`` is set) are auto-generated; gmail/microsoft
transports get the provider SMTP/IMAP hosts pinned read-only, Chatwoot-style.
"""

from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailure
from app.core.events import Actor, Event, EventNames, on
from app.core.security import decrypt_secret, encrypt_secret, new_token
from app.models.inbox import ChannelType, Inbox
from app.services import audit

DEFAULT_WIDGET_CONFIG: dict[str, Any] = {
    "accent_color": "#5b46e5",
    "greeting": "Hi! How can we help?",
    # Off by default so inbound conversations land in Unassigned for the team to
    # triage. With it on, a single-member workspace silently pre-claims every
    # conversation and the Unassigned queue is always empty. Toggle per inbox in
    # Settings -> Channels -> Configure.
    "auto_assign": False,
}

EMAIL_TRANSPORTS = frozenset(
    {"global", "smtp", "gmail", "microsoft", "ses", "resend", "postmark", "sendgrid", "mailgun"}
)
# transport → API-key-style secret it cannot work without.
EMAIL_TRANSPORT_SECRET = {
    "resend": "resend_api_key",
    "postmark": "postmark_server_token",
    "sendgrid": "sendgrid_api_key",
    "mailgun": "mailgun_api_key",
}
EMAIL_OAUTH_HOSTS = {
    "gmail": {"smtp": "smtp.gmail.com", "imap": "imap.gmail.com"},
    "microsoft": {"smtp": "smtp.office365.com", "imap": "outlook.office365.com"},
}
# Server-managed keys the dashboard never sends back; carried across updates.
EMAIL_GENERATED_CONFIG_KEYS = (
    "webhook_token",
    "forward_to",
    "imap_uid_cursor",
    "imap_last_poll_at",
    "reauth_required",
)
MIN_IMAP_POLL_MINUTES = 2
DEFAULT_IMAP_POLL_MINUTES = 3


def generate_widget_key() -> str:
    return "wk_" + new_token(18)


def set_secrets(inbox: Inbox, secrets: dict[str, Any] | None) -> None:
    """Store channel credentials Fernet-encrypted; empty/None clears them."""
    inbox.secrets_encrypted = encrypt_secret(json.dumps(secrets)) if secrets else None


def get_secrets(inbox: Inbox) -> dict[str, Any]:
    if not inbox.secrets_encrypted:
        return {}
    decrypted = json.loads(decrypt_secret(inbox.secrets_encrypted))
    return decrypted if isinstance(decrypted, dict) else {}


def embed_snippet(inbox: Inbox) -> str | None:
    """Copy-paste embed snippet for widget inboxes (loader served by this API)."""
    if inbox.channel_type != ChannelType.WIDGET or not inbox.widget_key:
        return None
    base = get_settings().public_base_url
    return (
        f'<script>window.SteptSettings={{workspaceKey:"{inbox.widget_key}"}}</script>\n'
        f'<script src="{base}/widget-assets/loader.js" async></script>'
    )


# ---------------------------------------------------------------------------
# email transport config (validation + auto-generation)
# ---------------------------------------------------------------------------


def _secret_names(secrets: dict[str, Any] | None) -> set[str]:
    return {key for key, value in (secrets or {}).items() if value not in (None, "")}


def _int_field(value: Any, default: int, errors: dict[str, str], field: str) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        errors[field] = "Must be a number"
        return default


def prepare_email_config(
    config: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    secret_keys: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Validate + normalize an email inbox config; raises ValidationFailure
    with a ``{"fields": {...}}`` map. Carries server-generated keys forward
    from ``previous`` and auto-fills ``webhook_token`` / ``forward_to``."""
    prepared = dict(config)
    previous = previous or {}
    for key in EMAIL_GENERATED_CONFIG_KEYS:
        if key not in prepared and key in previous:
            prepared[key] = previous[key]

    transport = prepared.get("transport") or "global"
    if transport not in EMAIL_TRANSPORTS:
        raise ValidationFailure(
            "Email inbox configuration is invalid",
            details={"fields": {"transport": f"Unknown transport: {transport}"}},
        )
    prepared["transport"] = transport
    errors: dict[str, str] = {}

    address = prepared.get("address")
    if transport != "global" and not (isinstance(address, str) and "@" in address):
        errors["address"] = "A valid from address is required"

    if transport in EMAIL_OAUTH_HOSTS:
        if not prepared.get("connection_id"):
            provider = "Google" if transport == "gmail" else "Microsoft"
            errors["connection_id"] = f"Connect a {provider} account first"
        hosts = EMAIL_OAUTH_HOSTS[transport]
        smtp = dict(prepared.get("smtp") or {})
        smtp.update({"host": hosts["smtp"], "port": 587, "security": "starttls"})
        prepared["smtp"] = smtp
        imap = dict(prepared.get("imap") or {})
        imap.update({"host": hosts["imap"], "port": 993})
        imap.setdefault("enabled", True)
        prepared["imap"] = imap

    if transport == "smtp":
        smtp = dict(prepared.get("smtp") or {})
        if not smtp.get("host"):
            errors["smtp.host"] = "SMTP host is required"
        smtp["port"] = _int_field(smtp.get("port"), 587, errors, "smtp.port")
        security = smtp.get("security") or "starttls"
        if security not in ("starttls", "tls", "none"):
            errors["smtp.security"] = "Must be starttls, tls, or none"
        smtp["security"] = security
        if smtp.get("username") and "smtp_password" not in secret_keys:
            errors["secrets.smtp_password"] = "SMTP password is required"
        prepared["smtp"] = smtp

    if transport == "ses":
        ses = dict(prepared.get("ses") or {})
        if not ses.get("region"):
            errors["ses.region"] = "AWS region is required"
        prepared["ses"] = ses
        for key in ("ses_access_key_id", "ses_secret_access_key"):
            if key not in secret_keys:
                errors[f"secrets.{key}"] = "Required for the SES transport"

    if transport == "mailgun":
        mailgun = dict(prepared.get("mailgun") or {})
        if not mailgun.get("domain"):
            errors["mailgun.domain"] = "Mailgun domain is required"
        base = mailgun.get("base") or "us"
        if base not in ("us", "eu"):
            errors["mailgun.base"] = "Must be us or eu"
        mailgun["base"] = base
        prepared["mailgun"] = mailgun

    required_secret = EMAIL_TRANSPORT_SECRET.get(transport)
    if required_secret and required_secret not in secret_keys:
        errors[f"secrets.{required_secret}"] = f"Required for the {transport} transport"

    imap = dict(prepared.get("imap") or {})
    if imap:
        if imap.get("enabled"):
            if transport not in EMAIL_OAUTH_HOSTS:
                if not imap.get("host"):
                    errors["imap.host"] = "IMAP host is required"
                if not imap.get("username"):
                    errors["imap.username"] = "IMAP username is required"
                if "imap_password" not in secret_keys:
                    errors["secrets.imap_password"] = "IMAP password is required"
            imap["port"] = _int_field(imap.get("port"), 993, errors, "imap.port")
            minutes = _int_field(
                imap.get("poll_minutes"), DEFAULT_IMAP_POLL_MINUTES, errors, "imap.poll_minutes"
            )
            if minutes < MIN_IMAP_POLL_MINUTES:
                errors["imap.poll_minutes"] = f"Minimum is {MIN_IMAP_POLL_MINUTES} minutes"
            imap["poll_minutes"] = minutes
        prepared["imap"] = imap

    if errors:
        raise ValidationFailure("Email inbox configuration is invalid", details={"fields": errors})

    if not prepared.get("webhook_token"):
        prepared["webhook_token"] = new_token(24)
    inbound_domain = get_settings().inbound_email_domain
    if inbound_domain and not prepared.get("forward_to"):
        prepared["forward_to"] = f"in-{os.urandom(6).hex()}@{inbound_domain}"
    return prepared


def _merge_email_secrets(
    existing: dict[str, Any], incoming: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Email secrets merge instead of replace so the dashboard can PATCH one
    field without re-pasting the rest. None → unchanged; {} → clear all;
    an empty value ("" / null) removes that one key."""
    if incoming is None or incoming == {}:
        return incoming
    merged = dict(existing)
    for key, value in incoming.items():
        if value in (None, ""):
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


async def create_inbox(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    name: str,
    channel_type: str,
    config: dict[str, Any] | None = None,
    secrets: dict[str, Any] | None = None,
    enabled: bool = True,
) -> Inbox:
    if channel_type not in ChannelType:
        raise ValidationFailure(f"Unknown channel type: {channel_type}")
    if channel_type == ChannelType.EMAIL:
        config = prepare_email_config(config or {}, secret_keys=_secret_names(secrets))
    inbox = Inbox(
        workspace_id=workspace_id,
        name=name.strip(),
        channel_type=channel_type,
        enabled=enabled,
        config=config or {},
        widget_key=generate_widget_key() if channel_type == ChannelType.WIDGET else None,
    )
    set_secrets(inbox, secrets)
    session.add(inbox)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="inbox.create",
        target_type="inbox",
        target_id=inbox.id,
        meta={"name": inbox.name, "channel_type": inbox.channel_type},
    )
    return inbox


async def list_inboxes(session: AsyncSession, workspace_id: str) -> list[Inbox]:
    result = await session.execute(
        select(Inbox).where(Inbox.workspace_id == workspace_id).order_by(Inbox.created_at)
    )
    return list(result.scalars())


async def get_inbox(session: AsyncSession, workspace_id: str, inbox_id: str) -> Inbox:
    inbox = await session.get(Inbox, inbox_id)
    if inbox is None or inbox.workspace_id != workspace_id:
        raise NotFoundError("Inbox not found")
    return inbox


async def update_inbox(
    session: AsyncSession,
    workspace_id: str,
    inbox_id: str,
    *,
    actor: Actor,
    name: str | None = None,
    enabled: bool | None = None,
    config: dict[str, Any] | None = None,
    secrets: dict[str, Any] | None = None,
) -> Inbox:
    inbox = await get_inbox(session, workspace_id, inbox_id)
    if name is not None:
        inbox.name = name.strip()
    if enabled is not None:
        inbox.enabled = enabled
    if inbox.channel_type == ChannelType.EMAIL and (config is not None or secrets is not None):
        existing_secrets = get_secrets(inbox)
        secrets = _merge_email_secrets(existing_secrets, secrets)
        effective = existing_secrets if secrets is None else secrets
        config = prepare_email_config(
            config if config is not None else dict(inbox.config),
            previous=dict(inbox.config),
            secret_keys=_secret_names(effective),
        )
    if config is not None:
        inbox.config = config
    if secrets is not None:  # {} clears, non-empty re-encrypts
        set_secrets(inbox, secrets)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="inbox.update",
        target_type="inbox",
        target_id=inbox.id,
        meta={"name": inbox.name},
    )
    return inbox


async def delete_inbox(
    session: AsyncSession, workspace_id: str, inbox_id: str, *, actor: Actor
) -> None:
    inbox = await get_inbox(session, workspace_id, inbox_id)
    await session.delete(inbox)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="inbox.delete",
        target_type="inbox",
        target_id=inbox_id,
        meta={"name": inbox.name, "channel_type": inbox.channel_type},
    )


async def ensure_default_widget_inbox(session: AsyncSession, workspace_id: str) -> Inbox:
    """Idempotent: the default widget inbox every workspace starts with."""
    existing = (
        (
            await session.execute(
                select(Inbox).where(
                    Inbox.workspace_id == workspace_id,
                    Inbox.channel_type == ChannelType.WIDGET,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing
    inbox = Inbox(
        workspace_id=workspace_id,
        name="Website widget",
        channel_type=ChannelType.WIDGET,
        enabled=True,
        config=dict(DEFAULT_WIDGET_CONFIG),
        widget_key=generate_widget_key(),
    )
    session.add(inbox)
    await session.flush()
    return inbox


@on(EventNames.WORKSPACE_CREATED)
async def _create_default_inbox(session: AsyncSession, event: Event) -> None:
    await ensure_default_widget_inbox(session, event.workspace_id)
