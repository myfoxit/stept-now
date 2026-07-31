"""Current-user endpoints: profile, memberships, notifications."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import CurrentUser, Db
from app.core.errors import UnauthorizedError
from app.core.security import hash_password, verify_password
from app.models.workspace import Workspace
from app.schemas.common import Msg
from app.schemas.user import ChangePasswordRequest, UserOut, UserUpdate
from app.schemas.workspace import MeResponse, MyMembership, WorkspaceOut
from app.services import workspaces as ws_service

router = APIRouter()


@router.get("/me", response_model=MeResponse)
async def get_me(user: CurrentUser, session: Db) -> MeResponse:
    memberships = await ws_service.list_my_workspaces(session, user)
    out: list[MyMembership] = []
    for m in memberships:
        workspace = await session.get(Workspace, m.workspace_id)
        if workspace is None:
            continue
        out.append(
            MyMembership(
                id=m.id,
                role=m.role,
                custom_role_id=m.custom_role_id,
                is_available=m.is_available,
                workspace=WorkspaceOut.model_validate(workspace),
                permissions=ws_service.membership_permissions(m),
            )
        )
    return MeResponse(user=UserOut.model_validate(user), memberships=out)


@router.patch("/me", response_model=UserOut)
async def update_me(body: UserUpdate, user: CurrentUser, session: Db) -> UserOut:
    if body.name is not None:
        user.name = body.name
    if body.avatar_url is not None:
        user.avatar_url = body.avatar_url
    if body.preferences is not None:
        merged = dict(user.preferences)
        merged.update(body.preferences)
        user.preferences = merged
    session.add(user)
    return UserOut.model_validate(user)


@router.post("/me/change-password", response_model=Msg)
async def change_password(body: ChangePasswordRequest, user: CurrentUser, session: Db) -> Msg:
    if not verify_password(body.current_password, user.password_hash):
        raise UnauthorizedError("Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    session.add(user)
    return Msg(message="Password changed")
