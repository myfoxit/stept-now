"""Inbox lifecycle: CRUD, encrypted channel secrets, widget embed snippet.

Every workspace gets a default widget inbox on creation (workspace.created
handler); other channels are added from settings (channels:manage).
"""

from __future__ import annotations

import json
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
    "accent_color": "#6366f1",
    "greeting": "Hi! How can we help?",
    # Off by default so inbound conversations land in Unassigned for the team to
    # triage. With it on, a single-member workspace silently pre-claims every
    # conversation and the Unassigned queue is always empty. Toggle per inbox in
    # Settings -> Channels -> Configure.
    "auto_assign": False,
}


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
