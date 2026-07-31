"""Canned responses API.

The empty router is pre-registered; add routes here, never touch the registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.schemas.canned_responses import (
    CannedResponseCreate,
    CannedResponseOut,
    CannedResponseUpdate,
)
from app.schemas.common import Msg
from app.services import canned_responses as canned_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


@router.get(
    "/canned-responses",
    response_model=list[CannedResponseOut],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_canned_responses(principal: Member, session: Db):
    responses = await canned_service.list_responses(session, principal.workspace.id)
    return [CannedResponseOut.model_validate(r) for r in responses]


@router.post(
    "/canned-responses",
    response_model=CannedResponseOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def create_canned_response(body: CannedResponseCreate, principal: Member, session: Db):
    response = await canned_service.create_response(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        shortcut=body.shortcut,
        content=body.content,
    )
    return CannedResponseOut.model_validate(response)


@router.patch(
    "/canned-responses/{response_id}",
    response_model=CannedResponseOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def update_canned_response(
    response_id: str, body: CannedResponseUpdate, principal: Member, session: Db
):
    response = await canned_service.update_response(
        session,
        principal.workspace.id,
        response_id,
        actor=_actor(principal),
        shortcut=body.shortcut,
        content=body.content,
    )
    return CannedResponseOut.model_validate(response)


@router.delete(
    "/canned-responses/{response_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def delete_canned_response(response_id: str, principal: Member, session: Db):
    await canned_service.delete_response(
        session, principal.workspace.id, response_id, actor=_actor(principal)
    )
    return Msg(message="Canned response deleted")
