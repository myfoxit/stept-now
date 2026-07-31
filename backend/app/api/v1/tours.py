"""Tours API (dashboard authoring).

The empty router is pre-registered under /w/{workspace_id}; routes are added here.
Reads require tours:read, mutations require tours:manage.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.schemas.common import Msg
from app.schemas.tours import (
    RecorderTokenOut,
    TourCreate,
    TourOut,
    TourStats,
    TourUpdate,
)
from app.services import tours as tours_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


@router.get(
    "/tours",
    response_model=list[TourOut],
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def list_tours(principal: Member, session: Db):
    tours = await tours_service.list_tours(session, principal.workspace.id)
    return [TourOut.model_validate(t) for t in tours]


@router.post(
    "/tours",
    response_model=TourOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def create_tour(body: TourCreate, principal: Member, session: Db):
    tour = await tours_service.create_tour(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        name=body.name,
        description=body.description,
        trigger=body.trigger.model_dump(),
        audience=body.audience.model_dump(),
        steps=[s.model_dump() for s in body.steps],
        theme=body.theme.model_dump(),
    )
    return TourOut.model_validate(tour)


@router.post(
    "/tours/recorder-token",
    response_model=RecorderTokenOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def create_recorder_token(principal: Member, session: Db):
    """Mint a short-lived token to paste into the Chrome tour-recorder extension."""
    token = tours_service.mint_recorder_token(principal.workspace.id, principal.actor_id)
    return RecorderTokenOut(token=token, expires_days=7)


@router.get(
    "/tours/{tour_id}",
    response_model=TourOut,
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def get_tour(tour_id: str, principal: Member, session: Db):
    tour = await tours_service.get_tour(session, principal.workspace.id, tour_id)
    return TourOut.model_validate(tour)


@router.patch(
    "/tours/{tour_id}",
    response_model=TourOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def update_tour(tour_id: str, body: TourUpdate, principal: Member, session: Db):
    tour = await tours_service.update_tour(
        session,
        principal.workspace.id,
        tour_id,
        actor=_actor(principal),
        name=body.name,
        description=body.description,
        trigger=body.trigger.model_dump() if body.trigger is not None else None,
        audience=body.audience.model_dump() if body.audience is not None else None,
        steps=[s.model_dump() for s in body.steps] if body.steps is not None else None,
        theme=body.theme.model_dump() if body.theme is not None else None,
    )
    return TourOut.model_validate(tour)


@router.delete(
    "/tours/{tour_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def delete_tour(tour_id: str, principal: Member, session: Db):
    await tours_service.delete_tour(
        session, principal.workspace.id, tour_id, actor=_actor(principal)
    )
    return Msg(message="Tour deleted")


@router.post(
    "/tours/{tour_id}/publish",
    response_model=TourOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def publish_tour(tour_id: str, principal: Member, session: Db):
    tour = await tours_service.publish_tour(
        session, principal.workspace.id, tour_id, actor=_actor(principal)
    )
    return TourOut.model_validate(tour)


@router.post(
    "/tours/{tour_id}/pause",
    response_model=TourOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def pause_tour(tour_id: str, principal: Member, session: Db):
    tour = await tours_service.pause_tour(
        session, principal.workspace.id, tour_id, actor=_actor(principal)
    )
    return TourOut.model_validate(tour)


@router.get(
    "/tours/{tour_id}/stats",
    response_model=TourStats,
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def tour_stats(tour_id: str, principal: Member, session: Db):
    return await tours_service.compute_stats(session, principal.workspace.id, tour_id)
