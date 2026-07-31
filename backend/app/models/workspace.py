"""Workspaces (tenants), memberships, custom roles, invitations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import GUID, Base, PortableJSON, UTCDateTime
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    # Free-form workspace settings: timezone, office_hours, widget identity secrets, etc.
    settings: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


class CustomRole(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "custom_roles"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_custom_roles_ws_name"),)

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(400))
    permissions: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)


class Membership(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_memberships_ws_user"),)

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # "owner" | "admin" | "agent" | "viewer" | "custom" (then custom_role_id is set)
    role: Mapped[str] = mapped_column(String(40), default="agent", nullable=False)
    custom_role_id: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("custom_roles.id", ondelete="SET NULL")
    )
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user = relationship("User", lazy="joined")
    custom_role = relationship("CustomRole", lazy="joined")


class Invitation(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "invitations"

    id: Mapped[str] = pk()
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="agent", nullable=False)
    custom_role_id: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("custom_roles.id", ondelete="SET NULL")
    )
    token: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    invited_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
