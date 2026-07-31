"""Widget public tours API.

Self-contained light auth (does NOT use app/api/widget/deps.py): the workspace is
resolved from the `widget_key` query param (matching Inbox.widget_key) and the
end-user contact, when known, from an optional `X-Widget-Token` (typ "widget").
The recorder endpoint authenticates with a recorder token instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import Db
from app.core.errors import NotFoundError, UnauthorizedError
from app.core.security import decode_token
from app.models.contact import Contact
from app.models.inbox import Inbox
from app.schemas.common import Msg
from app.schemas.tours import (
    RecorderTourIn,
    RecorderTourOut,
    WidgetTourEventIn,
    WidgetTourOut,
)
from app.services import tours as tours_service

router = APIRouter()


async def _resolve(session: Db, widget_key: str) -> tuple[str, Inbox]:
    """Resolve (workspace_id, inbox) from a public widget key."""
    inbox = (
        await session.execute(select(Inbox).where(Inbox.widget_key == widget_key))
    ).scalar_one_or_none()
    if inbox is None:
        raise NotFoundError("Unknown widget key")
    return inbox.workspace_id, inbox


async def _contact_from_token(request: Request, session: Db, workspace_id: str) -> Contact | None:
    """Best-effort contact resolution from an optional X-Widget-Token; a missing,
    invalid, or cross-workspace token simply yields an anonymous visitor."""
    token = request.headers.get("X-Widget-Token")
    if not token:
        return None
    try:
        payload = decode_token(token, "widget")
    except UnauthorizedError:
        return None
    if payload.get("ws") != workspace_id:
        return None
    contact_id = payload.get("sub")
    if not contact_id:
        return None
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.workspace_id != workspace_id:
        return None
    return contact


@router.get("/tours", response_model=list[WidgetTourOut])
async def list_widget_tours(request: Request, session: Db, widget_key: str, url: str):
    workspace_id, _inbox = await _resolve(session, widget_key)
    contact = await _contact_from_token(request, session, workspace_id)
    tours = await tours_service.deliverable_tours(session, workspace_id, url=url, contact=contact)
    return [WidgetTourOut.model_validate(t) for t in tours]


@router.post("/tours/recorder", response_model=RecorderTourOut, status_code=201)
async def create_recorder_tour(body: RecorderTourIn, session: Db):
    workspace_id, user_id = await tours_service.authorize_recorder(session, body.token)
    tour = await tours_service.create_tour_from_recorder(
        session,
        workspace_id,
        user_id=user_id,
        name=body.name,
        url_pattern=body.url_pattern,
        steps=[s.model_dump() for s in body.steps],
    )
    app_url = f"{get_settings().app_base_url}/tours/{tour.id}"
    return RecorderTourOut(id=tour.id, name=tour.name, app_url=app_url)


@router.post("/tours/{tour_id}/events", response_model=Msg)
async def record_widget_event(
    tour_id: str, body: WidgetTourEventIn, request: Request, session: Db, widget_key: str
):
    workspace_id, _inbox = await _resolve(session, widget_key)
    contact = await _contact_from_token(request, session, workspace_id)
    await tours_service.record_event(
        session,
        workspace_id,
        tour_id,
        event=body.event,
        step_index=body.step_index,
        contact_id=contact.id if contact is not None else None,
    )
    return Msg(message="recorded")
