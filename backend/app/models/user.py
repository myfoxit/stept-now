"""Users (global accounts) and refresh-token records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime
from app.models.base import TimestampMixin, pk


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    preferences: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


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
