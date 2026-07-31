"""Tags API: workspace-wide tag CRUD.

The empty router is pre-registered; add routes here, never touch the registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.schemas.common import Msg
from app.schemas.tags import TagCreate, TagOut, TagUpdate
from app.services import tags as tags_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


@router.get(
    "/tags",
    response_model=list[TagOut],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_tags(principal: Member, session: Db):
    tags = await tags_service.list_tags(session, principal.workspace.id)
    return [TagOut.model_validate(t) for t in tags]


@router.post(
    "/tags",
    response_model=TagOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def create_tag(body: TagCreate, principal: Member, session: Db):
    tag = await tags_service.create_tag(
        session, principal.workspace.id, actor=_actor(principal), name=body.name, color=body.color
    )
    return TagOut.model_validate(tag)


@router.patch(
    "/tags/{tag_id}",
    response_model=TagOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def update_tag(tag_id: str, body: TagUpdate, principal: Member, session: Db):
    tag = await tags_service.update_tag(
        session,
        principal.workspace.id,
        tag_id,
        actor=_actor(principal),
        name=body.name,
        color=body.color,
    )
    return TagOut.model_validate(tag)


@router.delete(
    "/tags/{tag_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def delete_tag(tag_id: str, principal: Member, session: Db):
    await tags_service.delete_tag(session, principal.workspace.id, tag_id, actor=_actor(principal))
    return Msg(message="Tag deleted")
