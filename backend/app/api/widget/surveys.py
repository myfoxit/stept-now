"""Widget public survey endpoints.

Self-contained light auth (like `widget/tours.py`, deliberately not shared): the
workspace comes from the `widget_key` query param and the contact, when known,
from an optional `X-Widget-Token`. Anonymous submissions are allowed and stored
with `contact_id = null`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.deps import Db
from app.core.errors import NotFoundError, UnauthorizedError
from app.core.security import decode_token
from app.models.contact import Contact
from app.models.inbox import ChannelType, Inbox
from app.schemas.surveys import WidgetSurveyAckOut, WidgetSurveyResponseIn
from app.services import surveys as surveys_service

router = APIRouter()


async def _resolve(session: Db, widget_key: str) -> tuple[str, Inbox]:
    """Resolve (workspace_id, inbox) from a public widget key.

    An unknown key, a disabled inbox, or a non-widget channel are all 404.
    """
    inbox = (
        await session.execute(select(Inbox).where(Inbox.widget_key == widget_key))
    ).scalar_one_or_none()
    if inbox is None or not inbox.enabled or inbox.channel_type != ChannelType.WIDGET:
        raise NotFoundError("Unknown widget key")
    return inbox.workspace_id, inbox


async def _contact_from_token(request: Request, session: Db, workspace_id: str) -> Contact | None:
    """Best-effort contact resolution; anything off yields an anonymous visitor."""
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


@router.post("/surveys/{survey_id}/responses", response_model=WidgetSurveyAckOut)
async def submit_response(
    survey_id: str,
    body: WidgetSurveyResponseIn,
    request: Request,
    session: Db,
    widget_key: str,
    url: str | None = None,
):
    workspace_id, _inbox = await _resolve(session, widget_key)
    survey = await surveys_service.get_survey(session, workspace_id, survey_id)
    contact = await _contact_from_token(request, session, workspace_id)
    await surveys_service.submit_survey_response(
        session,
        workspace_id,
        survey,
        contact.id if contact is not None else None,
        answers=[a.model_dump() for a in body.answers],
        completed=body.completed,
        meta={"url": url} if url else {},
    )
    return WidgetSurveyAckOut(ok=True, thanks_message=survey.thanks_message)
