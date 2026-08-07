"""Integration connections + per-workspace OAuth app credentials (W11).

The connection is the unit of "signed in with X": one authorized external
account, its encrypted tokens, and provider-specific routing metadata. Multiple
connections per provider are legal (two Gmail inboxes = two connections), so
consumers reference connections by id (`Inbox.config["connection_id"]`,
`KnowledgeSource.config["connection_id"]`) — never "the" provider connection.

App credentials are the operator's OAuth client per provider. Workspace-scoped
(a cloud tenant may bring their own app); resolution falls back to the
instance-wide env credentials in `app.core.config` when no row exists.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, PortableJSON
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class ConnectionStatus(enum.StrEnum):
    CONNECTED = "connected"
    REAUTH_REQUIRED = "reauth_required"
    ERROR = "error"
    REVOKED = "revoked"


class IntegrationConnection(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "integration_connections"

    id: Mapped[str] = pk()
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # "email" | "knowledge" | "channel" | "app" — denormalized from the catalog
    # so listings don't need it imported.
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=ConnectionStatus.CONNECTED, nullable=False
    )
    # Human handle for "Connected as …": the Gmail address, Slack team name,
    # Atlassian site name. Never used for routing.
    account_label: Mapped[str | None] = mapped_column(String(320))
    scopes: Mapped[list[Any]] = mapped_column(PortableJSON, default=list, nullable=False)
    # Fernet blobs via app.core.security.encrypt_secret/decrypt_secret.
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Provider routing facts: Atlassian {cloud_id, site_url, sites?}, Slack
    # {team_id, bot_user_id}, Notion {bot_id, workspace_name}, MS {tenant}.
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # Membership user id of whoever clicked Connect (audit trail; nullable for
    # connections created by automation).
    created_by: Mapped[str | None] = mapped_column(String(40))


class IntegrationAppCredential(TimestampMixin, WorkspaceScopedMixin, Base):
    """The operator's OAuth client for one provider (client id + secret).

    `extra` carries provider-specific non-OAuth material the operator gets from
    the same console (e.g. Slack's signing secret).
    """

    __tablename__ = "integration_app_credentials"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", name="uq_integration_credentials_provider"),
    )

    id: Mapped[str] = pk()
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    client_id: Mapped[str] = mapped_column(String(320), nullable=False)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
