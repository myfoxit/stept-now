"""Teams (agent groups for routing/assignment) and their memberships."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, UTCDateTime, utcnow
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class Team(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_teams_ws_name"),)

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Emoji shown next to the team name, e.g. "🎧".
    icon: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(String(400))


class TeamMember(WorkspaceScopedMixin, Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_members_pair"),)

    id: Mapped[str] = pk()
    team_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("teams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
