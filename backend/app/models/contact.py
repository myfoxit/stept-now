"""Contacts (end users). The Contact model itself is orchestrator-owned because
half the domains FK it; agent A owns notes/events/services/APIs on top of it."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime, utcnow
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class Contact(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "external_id", name="uq_contacts_ws_external"),
        Index("ix_contacts_ws_email", "workspace_id", "email"),
        Index("ix_contacts_ws_last_seen", "workspace_id", "last_seen_at"),
    )

    id: Mapped[str] = pk()
    # Stable id from the customer's own system (Intercom's user_id); null for visitors.
    external_id: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    # Custom attributes (plan, company, …) — filterable in segments/automations.
    attributes: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # True once identity was verified via HMAC (widget identity verification).
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Blocked contacts are dropped at channel ingress and cannot open new
    # conversations from the widget (app.services.contacts.assert_not_blocked).
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Set when this contact was merged away; the row is kept so old links resolve.
    merged_into_id: Mapped[str | None] = mapped_column(GUID, index=True)


class ContactNote(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "contact_notes"

    id: Mapped[str] = pk()
    contact_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("contacts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    author_id: Mapped[str | None] = mapped_column(GUID, ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)


class ContactEvent(WorkspaceScopedMixin, Base):
    """Timeline events (page views, custom events from the API/widget/tours)."""

    __tablename__ = "contact_events"
    __table_args__ = (Index("ix_contact_events_contact_created", "contact_id", "created_at"),)

    id: Mapped[str] = pk()
    contact_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("contacts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "page_view", "signup"
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
