"""Tours API (dashboard authoring).

The empty router is pre-registered under /w/{workspace_id}; routes are added here.
Reads require tours:read, mutations require tours:manage. Token-minting routes
(recorder / extension) are declared before `/tours/{tour_id}` so the path
parameter never swallows them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.pagination import OffsetPage, clamp_limit
from app.core.permissions import Perm
from app.core.security import (
    EXTENSION_TOKEN_TTL_DAYS,
    RECORDER_TOKEN_TTL_DAYS,
    create_extension_token,
    create_tour_preview_token,
)
from app.schemas.common import Msg
from app.schemas.tours import (
    ExtensionTokenOut,
    PreviewTokenOut,
    RecorderTokenOut,
    TourCreate,
    TourEventOut,
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
        kind=body.kind,
        trigger=body.trigger.model_dump(),
        audience=body.audience.model_dump(),
        schedule=body.schedule.model_dump(mode="json", exclude_none=True),
        frequency=body.frequency.model_dump(exclude_none=True),
        priority=body.priority,
        settings=body.settings.model_dump(),
        steps=[s.model_dump(by_alias=True) for s in body.steps],
        theme=body.theme.model_dump(exclude_none=True),
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
    return RecorderTokenOut(token=token, expires_days=RECORDER_TOKEN_TTL_DAYS)


@router.post(
    "/tours/extension-token",
    response_model=ExtensionTokenOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def create_extension_token_route(principal: Member, session: Db):
    """Mint the long-lived workspace-scoped token the logged-in extension stores.

    Membership + tours:manage are re-validated on every extension API call, so a
    revoked member's stored token stops working immediately."""
    token = create_extension_token(principal.workspace.id, principal.actor_id)
    return ExtensionTokenOut(token=token, expires_days=EXTENSION_TOKEN_TTL_DAYS)


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
        kind=body.kind,
        trigger=body.trigger.model_dump() if body.trigger is not None else None,
        audience=body.audience.model_dump() if body.audience is not None else None,
        schedule=(
            body.schedule.model_dump(mode="json", exclude_none=True)
            if body.schedule is not None
            else None
        ),
        frequency=(
            body.frequency.model_dump(exclude_none=True) if body.frequency is not None else None
        ),
        priority=body.priority,
        settings=body.settings.model_dump() if body.settings is not None else None,
        steps=(
            [s.model_dump(by_alias=True) for s in body.steps] if body.steps is not None else None
        ),
        theme=body.theme.model_dump(exclude_none=True) if body.theme is not None else None,
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


@router.post(
    "/tours/{tour_id}/preview-token",
    response_model=PreviewTokenOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def create_preview_token(tour_id: str, principal: Member, session: Db):
    """One-hour token scoped to this single tour: the widget plays it via
    `#stept-preview=<token>` regardless of status/trigger/frequency."""
    tour = await tours_service.get_tour(session, principal.workspace.id, tour_id)
    token = create_tour_preview_token(principal.workspace.id, tour.id)
    return PreviewTokenOut(token=token, expires_minutes=60)


@router.get(
    "/tours/{tour_id}/stats",
    response_model=TourStats,
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def tour_stats(tour_id: str, principal: Member, session: Db):
    return await tours_service.compute_stats(session, principal.workspace.id, tour_id)


@router.get(
    "/tours/{tour_id}/events",
    response_model=OffsetPage[TourEventOut],
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def tour_events(
    tour_id: str,
    principal: Member,
    session: Db,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Newest-first telemetry feed backing the analytics page's event table."""
    limit = clamp_limit(limit)
    rows, total = await tours_service.list_events(
        session, principal.workspace.id, tour_id, limit=limit, offset=offset
    )
    return OffsetPage[TourEventOut](
        items=[TourEventOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
