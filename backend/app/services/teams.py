"""Team CRUD + team membership (validated against workspace membership)."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.events import Actor
from app.models.team import Team, TeamMember
from app.models.user import User
from app.models.workspace import Membership
from app.services import audit


async def get_team(session: AsyncSession, workspace_id: str, team_id: str) -> Team:
    team = await session.get(Team, team_id)
    if team is None or team.workspace_id != workspace_id:
        raise NotFoundError("Team not found")
    return team


async def list_teams(session: AsyncSession, workspace_id: str) -> list[Team]:
    result = await session.execute(
        select(Team).where(Team.workspace_id == workspace_id).order_by(Team.name)
    )
    return list(result.scalars())


async def members_map(
    session: AsyncSession, workspace_id: str, team_ids: list[str]
) -> dict[str, list[User]]:
    """Batch-load team members: {team_id: [User, ...]}."""
    if not team_ids:
        return {}
    result = await session.execute(
        select(TeamMember.team_id, User)
        .join(User, User.id == TeamMember.user_id)
        .where(TeamMember.workspace_id == workspace_id, TeamMember.team_id.in_(team_ids))
        .order_by(User.name)
    )
    grouped: dict[str, list[User]] = {}
    for team_id, user in result.all():
        grouped.setdefault(team_id, []).append(user)
    return grouped


async def create_team(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    name: str,
    icon: str | None = None,
    description: str | None = None,
) -> Team:
    team = Team(workspace_id=workspace_id, name=name.strip(), icon=icon, description=description)
    session.add(team)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("A team with this name already exists") from exc
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="team.create",
        target_type="team",
        target_id=team.id,
        meta={"name": team.name},
    )
    return team


async def update_team(
    session: AsyncSession,
    workspace_id: str,
    team_id: str,
    *,
    actor: Actor,
    name: str | None = None,
    icon: str | None = None,
    description: str | None = None,
) -> Team:
    team = await get_team(session, workspace_id, team_id)
    if name is not None:
        team.name = name.strip()
    if icon is not None:
        team.icon = icon
    if description is not None:
        team.description = description
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("A team with this name already exists") from exc
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="team.update",
        target_type="team",
        target_id=team.id,
        meta={"name": team.name},
    )
    return team


async def delete_team(
    session: AsyncSession, workspace_id: str, team_id: str, *, actor: Actor
) -> None:
    team = await get_team(session, workspace_id, team_id)
    # Explicit link cleanup (SQLite doesn't enforce FK cascades by default).
    await session.execute(delete(TeamMember).where(TeamMember.team_id == team_id))
    await session.delete(team)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="team.delete",
        target_type="team",
        target_id=team_id,
        meta={"name": team.name},
    )


async def add_member(
    session: AsyncSession, workspace_id: str, team_id: str, *, actor: Actor, user_id: str
) -> Team:
    """Add a workspace member to the team (idempotent)."""
    team = await get_team(session, workspace_id, team_id)
    is_member = (
        await session.execute(
            select(Membership.id).where(
                Membership.workspace_id == workspace_id, Membership.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if is_member is None:
        raise BadRequestError("User is not a member of this workspace")
    existing = (
        await session.execute(
            select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(TeamMember(workspace_id=workspace_id, team_id=team_id, user_id=user_id))
        await session.flush()
        await audit.record(
            session,
            workspace_id,
            actor=actor,
            action="team.member_add",
            target_type="team",
            target_id=team_id,
            meta={"user_id": user_id},
        )
    return team


async def remove_member(
    session: AsyncSession, workspace_id: str, team_id: str, user_id: str, *, actor: Actor
) -> Team:
    """Remove a user from the team (idempotent)."""
    team = await get_team(session, workspace_id, team_id)
    existing = (
        await session.execute(
            select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()
        await audit.record(
            session,
            workspace_id,
            actor=actor,
            action="team.member_remove",
            target_type="team",
            target_id=team_id,
            meta={"user_id": user_id},
        )
    return team
