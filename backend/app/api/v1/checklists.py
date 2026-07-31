"""Checklists API (dashboard authoring).

The empty router is pre-registered under /w/{workspace_id}; routes live here.
Checklists are a DAP experience, so they reuse the tour permissions:
reads require tours:read, mutations require tours:manage.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.schemas.checklists import (
    ChecklistCreate,
    ChecklistOut,
    ChecklistStats,
    ChecklistUpdate,
)
from app.schemas.common import Msg
from app.services import checklists as checklists_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


@router.get(
    "/checklists",
    response_model=list[ChecklistOut],
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def list_checklists(principal: Member, session: Db):
    rows = await checklists_service.list_checklists(session, principal.workspace.id)
    return [ChecklistOut.model_validate(c) for c in rows]


@router.post(
    "/checklists",
    response_model=ChecklistOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def create_checklist(body: ChecklistCreate, principal: Member, session: Db):
    checklist = await checklists_service.create_checklist(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        name=body.name,
        description=body.description,
        items=[i.model_dump() for i in body.items],
        trigger=body.trigger.model_dump(),
        audience=body.audience.model_dump(),
        theme=body.theme.model_dump(),
        launcher=body.launcher.model_dump(),
        priority=body.priority,
    )
    return ChecklistOut.model_validate(checklist)


@router.get(
    "/checklists/{checklist_id}",
    response_model=ChecklistOut,
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def get_checklist(checklist_id: str, principal: Member, session: Db):
    checklist = await checklists_service.get_checklist(
        session, principal.workspace.id, checklist_id
    )
    return ChecklistOut.model_validate(checklist)


@router.patch(
    "/checklists/{checklist_id}",
    response_model=ChecklistOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def update_checklist(
    checklist_id: str, body: ChecklistUpdate, principal: Member, session: Db
):
    checklist = await checklists_service.update_checklist(
        session,
        principal.workspace.id,
        checklist_id,
        actor=_actor(principal),
        name=body.name,
        description=body.description,
        items=[i.model_dump() for i in body.items] if body.items is not None else None,
        trigger=body.trigger.model_dump() if body.trigger is not None else None,
        audience=body.audience.model_dump() if body.audience is not None else None,
        theme=body.theme.model_dump() if body.theme is not None else None,
        launcher=body.launcher.model_dump() if body.launcher is not None else None,
        priority=body.priority,
    )
    return ChecklistOut.model_validate(checklist)


@router.delete(
    "/checklists/{checklist_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def delete_checklist(checklist_id: str, principal: Member, session: Db):
    await checklists_service.delete_checklist(
        session, principal.workspace.id, checklist_id, actor=_actor(principal)
    )
    return Msg(message="Checklist deleted")


@router.post(
    "/checklists/{checklist_id}/publish",
    response_model=ChecklistOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def publish_checklist(checklist_id: str, principal: Member, session: Db):
    checklist = await checklists_service.publish_checklist(
        session, principal.workspace.id, checklist_id, actor=_actor(principal)
    )
    return ChecklistOut.model_validate(checklist)


@router.post(
    "/checklists/{checklist_id}/pause",
    response_model=ChecklistOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def pause_checklist(checklist_id: str, principal: Member, session: Db):
    checklist = await checklists_service.pause_checklist(
        session, principal.workspace.id, checklist_id, actor=_actor(principal)
    )
    return ChecklistOut.model_validate(checklist)


@router.get(
    "/checklists/{checklist_id}/stats",
    response_model=ChecklistStats,
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def checklist_stats(checklist_id: str, principal: Member, session: Db):
    return await checklists_service.compute_stats(session, principal.workspace.id, checklist_id)
