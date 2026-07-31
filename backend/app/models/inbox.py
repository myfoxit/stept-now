"""Inboxes (channel instances) and contact_inboxes (per-channel contact identity).

Follows Chatwoot's design (docs/research/chatwoot.md): the inbox is channel-
agnostic — only its `channel_type` + config differ; `ContactInbox` is the
identity spine linking a contact to one channel via a per-inbox `source_id`.
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class ChannelType(enum.StrEnum):
    WIDGET = "widget"
    EMAIL = "email"
    SLACK = "slack"
    TELEGRAM = "telegram"
    API = "api"
    WHATSAPP = "whatsapp"
    MESSENGER = "messenger"
    INSTAGRAM = "instagram"
    SMS = "sms"
    LINE = "line"


class Inbox(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "inboxes"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(30), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Public config: widget theming {accent_color, launcher_position, greeting,
    # require_identity, office_hours, auto_assign}, email {address}, etc.
    config: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # Fernet-encrypted JSON blob of channel credentials (bot tokens, SMTP passwords).
    # Read/write via app.services.inboxes.get_secrets()/set_secrets() — never returned.
    secrets_encrypted: Mapped[str | None] = mapped_column(Text)
    # Public embed key for widget inboxes ("wk_…"); null for other channels.
    widget_key: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)


class ContactInbox(TimestampMixin, WorkspaceScopedMixin, Base):
    """A contact's identity on one channel (Chatwoot's contact_inboxes).

    source_id is the channel-native identity: widget visitor uuid, email
    address, telegram chat id, … Unique per inbox.
    """

    __tablename__ = "contact_inboxes"
    __table_args__ = (
        UniqueConstraint("inbox_id", "source_id", name="uq_contact_inboxes_inbox_source"),
    )

    id: Mapped[str] = pk()
    contact_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("contacts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    inbox_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("inboxes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_id: Mapped[str] = mapped_column(String(320), nullable=False)
    # True once the identity was verified via HMAC (widget identity verification).
    hmac_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
