"""Users (global accounts), linked social identities, and refresh-token records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime, utcnow
from app.models.base import TimestampMixin, pk


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # NULL ⇒ social-login-only account (signed up via Google/GitHub); such users
    # can set a password later through the password-reset flow.
    password_hash: Mapped[str | None] = mapped_column(String(300))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Dashboard language. NULL ⇒ follow the browser's Accept-Language; an
    # explicit value always wins, because someone who picked Japanese means it
    # whatever laptop they are borrowing. Codes come from `app.core.i18n`.
    locale: Mapped[str | None] = mapped_column(String(12))
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    preferences: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # Password reset: sha256 of the emailed token, never the token itself, so a
    # database read cannot mint a reset. At most one is outstanding — issuing a
    # new one overwrites the old — and it is cleared the moment it is spent or
    # the password changes by any other route, which is what makes it single-use.
    password_reset_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    password_reset_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class UserIdentity(Base):
    """External identity-provider account linked to a user (social login).

    One row per (provider, provider_user_id). A user may hold several identities
    (Google and GitHub) alongside — or instead of — a password.
    """

    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_user_identity_provider_account"),
    )

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Email as asserted by the provider at link time (informational; the User row
    # keeps the canonical address).
    email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class RefreshToken(Base):
    """One row per issued refresh token (jti). Rotation chains via replaced_by;
    reuse of a rotated token revokes the whole family (theft detection)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = pk()  # the jti
    user_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    replaced_by: Mapped[str | None] = mapped_column(GUID)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(400))
    ip: Mapped[str | None] = mapped_column(String(64))
