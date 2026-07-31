"""Segments API: saved contact filters + membership preview.

The empty router is pre-registered; add routes here, never touch the registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.pagination import clamp_limit
from app.core.permissions import Perm
from app.schemas.common import Msg
from app.schemas.contacts import ContactOut
from app.schemas.segments import SegmentCreate, SegmentOut, SegmentUpdate
from app.schemas.tags import TagOut
from app.services import contacts as contacts_service
from app.services import segments as segments_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


@router.get(
    "/segments",
    response_model=list[SegmentOut],
    dependencies=[Depends(require_perm(Perm.CONTACTS_READ))],
)
async def list_segments(principal: Member, session: Db):
    segments = await segments_service.list_segments(session, principal.workspace.id)
    return [SegmentOut.model_validate(s) for s in segments]


@router.post(
    "/segments",
    response_model=SegmentOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def create_segment(body: SegmentCreate, principal: Member, session: Db):
    segment = await segments_service.create_segment(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        name=body.name,
        filters=[f.model_dump() for f in body.filters],
    )
    return SegmentOut.model_validate(segment)


@router.patch(
    "/segments/{segment_id}",
    response_model=SegmentOut,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def update_segment(segment_id: str, body: SegmentUpdate, principal: Member, session: Db):
    segment = await segments_service.update_segment(
        session,
        principal.workspace.id,
        segment_id,
        actor=_actor(principal),
        name=body.name,
        filters=[f.model_dump() for f in body.filters] if body.filters is not None else None,
    )
    return SegmentOut.model_validate(segment)


@router.delete(
    "/segments/{segment_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.CONTACTS_WRITE))],
)
async def delete_segment(segment_id: str, principal: Member, session: Db):
    await segments_service.delete_segment(
        session, principal.workspace.id, segment_id, actor=_actor(principal)
    )
    return Msg(message="Segment deleted")


@router.get(
    "/segments/{segment_id}/contacts",
    response_model=list[ContactOut],
    dependencies=[Depends(require_perm(Perm.CONTACTS_READ))],
)
async def preview_segment(
    segment_id: str, principal: Member, session: Db, limit: int | None = None
):
    """Preview the contacts currently matching the segment (capped list)."""
    segment = await segments_service.get_segment(session, principal.workspace.id, segment_id)
    contacts = await segments_service.apply_filters(
        session, principal.workspace.id, segment.filters
    )
    contacts = contacts[: clamp_limit(limit, default=50, maximum=200)]
    tag_map = await contacts_service.tags_for_contacts(
        session, principal.workspace.id, [c.id for c in contacts]
    )
    out = []
    for contact in contacts:
        item = ContactOut.model_validate(contact)
        item.tags = [TagOut.model_validate(t) for t in tag_map.get(contact.id, [])]
        out.append(item)
    return out
