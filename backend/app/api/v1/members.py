"""Members, invitations, custom roles, permission catalog."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, require_perm
from app.core.events import Actor
from app.core.permissions import BUILTIN_ROLES, Perm
from app.schemas.common import Msg
from app.schemas.workspace import (
    InvitationCreate,
    InvitationOut,
    MembershipOut,
    MemberUpdate,
    PermissionCatalogOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
)
from app.services import members as members_service

router = APIRouter()


def _actor(principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


@router.get("/members", response_model=list[MembershipOut])
async def list_members(principal: Member, session: Db):
    members = await members_service.list_members(session, principal.workspace.id)
    return [MembershipOut.model_validate(m) for m in members]


@router.patch(
    "/members/{member_id}",
    response_model=MembershipOut,
    dependencies=[Depends(require_perm(Perm.MEMBERS_MANAGE))],
)
async def update_member(member_id: str, body: MemberUpdate, principal: Member, session: Db):
    membership = await members_service.update_member(
        session,
        principal.workspace.id,
        member_id,
        actor=_actor(principal),
        role=body.role,
        custom_role_id=body.custom_role_id,
        is_available=body.is_available,
    )
    return MembershipOut.model_validate(membership)


@router.delete(
    "/members/{member_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.MEMBERS_MANAGE))],
)
async def remove_member(member_id: str, principal: Member, session: Db):
    await members_service.remove_member(
        session, principal.workspace.id, member_id, actor=_actor(principal)
    )
    return Msg(message="Member removed")


@router.get("/invitations", response_model=list[InvitationOut])
async def list_invitations(principal: Member, session: Db):
    invitations = await members_service.list_invitations(session, principal.workspace.id)
    return [InvitationOut.model_validate(i) for i in invitations]


@router.post(
    "/invitations",
    response_model=InvitationOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.MEMBERS_MANAGE))],
)
async def create_invitation(body: InvitationCreate, principal: Member, session: Db):
    invitation = await members_service.create_invitation(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        workspace_name=principal.workspace.name,
        email=body.email,
        role=body.role,
        custom_role_id=body.custom_role_id,
    )
    return InvitationOut.model_validate(invitation)


@router.delete(
    "/invitations/{invitation_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.MEMBERS_MANAGE))],
)
async def revoke_invitation(invitation_id: str, principal: Member, session: Db):
    await members_service.revoke_invitation(
        session, principal.workspace.id, invitation_id, actor=_actor(principal)
    )
    return Msg(message="Invitation revoked")


# --- custom roles -----------------------------------------------------------


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(principal: Member, session: Db):
    roles = await members_service.list_roles(session, principal.workspace.id)
    return [RoleOut.model_validate(r) for r in roles]


@router.get("/roles/catalog", response_model=PermissionCatalogOut)
async def permission_catalog(principal: Member):
    return PermissionCatalogOut(
        permissions=[p.value for p in Perm],
        builtin_roles={
            name: sorted(p.value for p in perms) for name, perms in BUILTIN_ROLES.items()
        },
    )


@router.post(
    "/roles",
    response_model=RoleOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.ROLES_MANAGE))],
)
async def create_role(body: RoleCreate, principal: Member, session: Db):
    role = await members_service.create_role(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        name=body.name,
        description=body.description,
        permissions=body.permissions,
    )
    return RoleOut.model_validate(role)


@router.patch(
    "/roles/{role_id}",
    response_model=RoleOut,
    dependencies=[Depends(require_perm(Perm.ROLES_MANAGE))],
)
async def update_role(role_id: str, body: RoleUpdate, principal: Member, session: Db):
    role = await members_service.update_role(
        session,
        principal.workspace.id,
        role_id,
        actor=_actor(principal),
        name=body.name,
        description=body.description,
        permissions=body.permissions,
    )
    return RoleOut.model_validate(role)


@router.delete(
    "/roles/{role_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.ROLES_MANAGE))],
)
async def delete_role(role_id: str, principal: Member, session: Db):
    await members_service.delete_role(
        session, principal.workspace.id, role_id, actor=_actor(principal)
    )
    return Msg(message="Role deleted")
