"""Widget public checklist endpoints.

Self-contained light auth (like `widget/tours.py`, deliberately not shared): the
workspace is resolved from the `widget_key` query param (matching a live widget
`Inbox`) and the end-user contact, when known, from an optional `X-Widget-Token`
(typ "widget"). Anonymous visitors are accepted and simply keep their progress
client-side — the server stores nothing for them.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.deps import Db
from app.core.errors import NotFoundError, UnauthorizedError
from app.core.security import decode_token
from app.models.contact import Contact
from app.models.inbox import ChannelType, Inbox
from app.schemas.checklists import (
    WidgetAckOut,
    WidgetChecklistProgressIn,
    WidgetChecklistProgressOut,
)
from app.services import checklists as checklists_service

router = APIRouter()


async def _resolve(session: Db, widget_key: str) -> tuple[str, Inbox]:
    """Resolve (workspace_id, inbox) from a public widget key.

    An unknown key, a disabled inbox, or a non-widget channel are all 404 — the
    public surface never confirms that a key exists but is switched off.
    """
    inbox = (
        await session.execute(select(Inbox).where(Inbox.widget_key == widget_key))
    ).scalar_one_or_none()
    if inbox is None or not inbox.enabled or inbox.channel_type != ChannelType.WIDGET:
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


@router.post("/checklists/{checklist_id}/progress", response_model=WidgetChecklistProgressOut)
async def record_progress(
    checklist_id: str,
    body: WidgetChecklistProgressIn,
    request: Request,
    session: Db,
    widget_key: str,
):
    workspace_id, _inbox = await _resolve(session, widget_key)
    checklist = await checklists_service.get_checklist(session, workspace_id, checklist_id)
    contact = await _contact_from_token(request, session, workspace_id)
    if contact is None:
        # Anonymous progress lives in the widget's local storage.
        return WidgetChecklistProgressOut(stored=False)
    progress = await checklists_service.record_checklist_progress(
        session,
        workspace_id,
        checklist,
        contact.id,
        item_id=body.item_id,
        done=body.done,
    )
    return WidgetChecklistProgressOut(
        stored=True,
        item_state=dict(progress.item_state or {}),
        dismissed=progress.dismissed_at is not None,
        completed=progress.completed_at is not None,
    )


@router.post("/checklists/{checklist_id}/dismiss", response_model=WidgetAckOut)
async def dismiss(checklist_id: str, request: Request, session: Db, widget_key: str):
    workspace_id, _inbox = await _resolve(session, widget_key)
    checklist = await checklists_service.get_checklist(session, workspace_id, checklist_id)
    contact = await _contact_from_token(request, session, workspace_id)
    if contact is None:
        return WidgetAckOut(ok=True, stored=False)
    await checklists_service.dismiss_checklist(session, workspace_id, checklist, contact.id)
    return WidgetAckOut(ok=True, stored=True)
