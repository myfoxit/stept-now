"""Workspace CRUD + invitation acceptance (global scope)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, Db, Member, require_perm
from app.core.permissions import Perm
from app.models.workspace import Workspace
from app.schemas.common import Msg
from app.schemas.workspace import (
    IdentitySecretOut,
    InvitationAccept,
    MyMembership,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)
from app.services import members as members_service
from app.services import workspaces as ws_service

router = APIRouter()


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
async def create_workspace(body: WorkspaceCreate, user: CurrentUser, session: Db):
    workspace = await ws_service.create_workspace(session, user, name=body.name)
    return WorkspaceOut.model_validate(workspace)


@router.get("/w/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(principal: Member):
    return WorkspaceOut.model_validate(principal.workspace)


@router.get(
    "/w/{workspace_id}/identity-secret",
    response_model=IdentitySecretOut,
    dependencies=[Depends(require_perm(Perm.WORKSPACE_MANAGE))],
)
async def get_identity_secret(principal: Member):
    """The widget identity-verification key — kept out of `WorkspaceOut` because
    it forges any visitor's identity, so reading it takes more than membership."""
    return IdentitySecretOut(
        identity_secret=str(principal.workspace.settings.get("identity_secret") or "")
    )


@router.patch(
    "/w/{workspace_id}",
    response_model=WorkspaceOut,
    dependencies=[Depends(require_perm(Perm.WORKSPACE_MANAGE))],
)
async def update_workspace(body: WorkspaceUpdate, principal: Member, session: Db):
    workspace = await ws_service.update_workspace(
        session,
        principal.workspace,
        name=body.name,
        logo_url=body.logo_url,
        settings_patch=body.settings,
    )
    return WorkspaceOut.model_validate(workspace)


@router.delete(
    "/w/{workspace_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.WORKSPACE_DELETE))],
)
async def delete_workspace(principal: Member, session: Db):
    await ws_service.delete_workspace(session, principal.workspace.id)
    return Msg(message="Workspace deleted")


@router.post("/invitations/accept", response_model=MyMembership)
async def accept_invitation(body: InvitationAccept, user: CurrentUser, session: Db):
    membership = await members_service.accept_invitation(session, user, token=body.token)
    workspace = await session.get(Workspace, membership.workspace_id)
    assert workspace is not None
    return MyMembership(
        id=membership.id,
        role=membership.role,
        custom_role_id=membership.custom_role_id,
        is_available=membership.is_available,
        workspace=WorkspaceOut.model_validate(workspace),
        permissions=ws_service.membership_permissions(membership),
    )
