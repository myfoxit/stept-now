"""Macros API: CRUD (personal/global visibility rules) + run-on-conversation.

Listing returns global macros plus the caller's own personal ones. Creating a
global macro (or editing/deleting global macros) needs conversations:manage;
personal macros are only ever visible/editable to their creator — anyone else
gets a 404, exactly like the listing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, Principal, require_perm
from app.core.errors import ForbiddenError, NotFoundError
from app.core.events import Actor
from app.core.permissions import Perm
from app.models.macro import Macro, MacroVisibility
from app.schemas.common import Msg
from app.schemas.macros import (
    MacroActionResult,
    MacroCreate,
    MacroOut,
    MacroRunOut,
    MacroRunRequest,
    MacroUpdate,
)
from app.services import conversations as conversations_service
from app.services import macros as macros_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


def _user_id(principal: Principal) -> str | None:
    return principal.user.id if principal.user is not None else None


def _ensure_visible(principal: Principal, macro: Macro) -> None:
    """Someone else's personal macro does not exist for this caller."""
    if macro.visibility == MacroVisibility.PERSONAL.value and macro.created_by != _user_id(
        principal
    ):
        raise NotFoundError("Macro not found")


def _ensure_can_modify(principal: Principal, macro: Macro) -> None:
    _ensure_visible(principal, macro)
    if macro.visibility == MacroVisibility.GLOBAL.value and not principal.has(
        Perm.CONVERSATIONS_MANAGE
    ):
        raise ForbiddenError("Managing global macros requires conversations:manage")


@router.get(
    "/macros",
    response_model=list[MacroOut],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_macros(principal: Member, session: Db) -> list[MacroOut]:
    macros = await macros_service.list_macros(
        session, principal.workspace.id, user_id=_user_id(principal)
    )
    return [MacroOut.model_validate(macro) for macro in macros]


@router.post(
    "/macros",
    response_model=MacroOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_WRITE))],
)
async def create_macro(body: MacroCreate, principal: Member, session: Db) -> MacroOut:
    if body.visibility == MacroVisibility.GLOBAL.value and not principal.has(
        Perm.CONVERSATIONS_MANAGE
    ):
        raise ForbiddenError("Creating global macros requires conversations:manage")
    macro = await macros_service.create_macro(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        name=body.name,
        actions=[action.model_dump() for action in body.actions],
        visibility=body.visibility,
    )
    return MacroOut.model_validate(macro)


@router.patch(
    "/macros/{macro_id}",
    response_model=MacroOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_WRITE))],
)
async def update_macro(
    macro_id: str, body: MacroUpdate, principal: Member, session: Db
) -> MacroOut:
    macro = await macros_service.get_macro(session, principal.workspace.id, macro_id)
    _ensure_can_modify(principal, macro)
    if (
        body.visibility == MacroVisibility.GLOBAL.value
        and macro.visibility == MacroVisibility.PERSONAL.value
        and not principal.has(Perm.CONVERSATIONS_MANAGE)
    ):
        raise ForbiddenError("Publishing a macro globally requires conversations:manage")
    macro = await macros_service.update_macro(
        session,
        principal.workspace.id,
        macro,
        actor=_actor(principal),
        name=body.name,
        actions=(
            [action.model_dump() for action in body.actions] if body.actions is not None else None
        ),
        visibility=body.visibility,
    )
    return MacroOut.model_validate(macro)


@router.delete(
    "/macros/{macro_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_WRITE))],
)
async def delete_macro(macro_id: str, principal: Member, session: Db) -> Msg:
    macro = await macros_service.get_macro(session, principal.workspace.id, macro_id)
    _ensure_can_modify(principal, macro)
    await macros_service.delete_macro(
        session, principal.workspace.id, macro, actor=_actor(principal)
    )
    return Msg(message="Macro deleted")


@router.post(
    "/macros/{macro_id}/run",
    response_model=MacroRunOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def run_macro(
    macro_id: str, body: MacroRunRequest, principal: Member, session: Db
) -> MacroRunOut:
    if principal.user is None:
        raise ForbiddenError("Only workspace members can run macros")
    macro = await macros_service.get_macro(session, principal.workspace.id, macro_id)
    _ensure_visible(principal, macro)
    conversation = await conversations_service.get_conversation(
        session, principal.workspace.id, body.conversation_id
    )
    results = await macros_service.run_macro(
        session,
        principal.workspace.id,
        macro,
        conversation,
        user=principal.user,
        member_name=principal.user.name,
    )
    return MacroRunOut(results=[MacroActionResult(**result) for result in results])
