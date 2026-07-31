"""Teams API: team CRUD + membership management.

The empty router is pre-registered; add routes here, never touch the registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.models.team import Team
from app.schemas.common import Msg
from app.schemas.teams import TeamCreate, TeamMemberAdd, TeamOut, TeamUpdate
from app.schemas.user import UserOut
from app.services import teams as teams_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


async def _team_out(session: AsyncSession, workspace_id: str, team: Team) -> TeamOut:
    members = await teams_service.members_map(session, workspace_id, [team.id])
    return TeamOut(
        id=team.id,
        name=team.name,
        icon=team.icon,
        description=team.description,
        members=[UserOut.model_validate(u) for u in members.get(team.id, [])],
        created_at=team.created_at,
    )


@router.get(
    "/teams",
    response_model=list[TeamOut],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_teams(principal: Member, session: Db):
    teams = await teams_service.list_teams(session, principal.workspace.id)
    members = await teams_service.members_map(
        session, principal.workspace.id, [t.id for t in teams]
    )
    return [
        TeamOut(
            id=t.id,
            name=t.name,
            icon=t.icon,
            description=t.description,
            members=[UserOut.model_validate(u) for u in members.get(t.id, [])],
            created_at=t.created_at,
        )
        for t in teams
    ]


@router.post(
    "/teams",
    response_model=TeamOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def create_team(body: TeamCreate, principal: Member, session: Db):
    team = await teams_service.create_team(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        name=body.name,
        icon=body.icon,
        description=body.description,
    )
    return await _team_out(session, principal.workspace.id, team)


@router.patch(
    "/teams/{team_id}",
    response_model=TeamOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def update_team(team_id: str, body: TeamUpdate, principal: Member, session: Db):
    team = await teams_service.update_team(
        session,
        principal.workspace.id,
        team_id,
        actor=_actor(principal),
        name=body.name,
        icon=body.icon,
        description=body.description,
    )
    return await _team_out(session, principal.workspace.id, team)


@router.delete(
    "/teams/{team_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def delete_team(team_id: str, principal: Member, session: Db):
    await teams_service.delete_team(
        session, principal.workspace.id, team_id, actor=_actor(principal)
    )
    return Msg(message="Team deleted")


@router.post(
    "/teams/{team_id}/members",
    response_model=TeamOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def add_team_member(team_id: str, body: TeamMemberAdd, principal: Member, session: Db):
    team = await teams_service.add_member(
        session, principal.workspace.id, team_id, actor=_actor(principal), user_id=body.user_id
    )
    return await _team_out(session, principal.workspace.id, team)


@router.delete(
    "/teams/{team_id}/members/{user_id}",
    response_model=TeamOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def remove_team_member(team_id: str, user_id: str, principal: Member, session: Db):
    team = await teams_service.remove_member(
        session, principal.workspace.id, team_id, user_id, actor=_actor(principal)
    )
    return await _team_out(session, principal.workspace.id, team)
