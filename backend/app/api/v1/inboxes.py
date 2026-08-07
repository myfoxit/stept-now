"""Inboxes API: channel instances per workspace.

Reading requires conversations:read; mutations require channels:manage.
Secrets are write-only — responses expose has_secrets plus the widget embed
snippet for widget inboxes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_hours import is_open
from app.core.db import utcnow
from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.models.inbox import Inbox
from app.schemas.business_hours import (
    WorkingHourOut,
    WorkingHoursOut,
    WorkingHoursUpdate,
)
from app.schemas.common import Msg
from app.schemas.inboxes import InboxCreate, InboxOut, InboxUpdate
from app.services import business_hours as hours_service
from app.services import inboxes as inboxes_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


def _out(inbox: Inbox) -> InboxOut:
    return InboxOut(
        id=inbox.id,
        name=inbox.name,
        channel_type=inbox.channel_type,
        enabled=inbox.enabled,
        config=dict(inbox.config),
        widget_key=inbox.widget_key,
        has_secrets=inbox.secrets_encrypted is not None,
        embed_snippet=inboxes_service.embed_snippet(inbox),
        created_at=inbox.created_at,
        updated_at=inbox.updated_at,
    )


@router.get(
    "/inboxes",
    response_model=list[InboxOut],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_inboxes(principal: Member, session: Db) -> list[InboxOut]:
    inboxes = await inboxes_service.list_inboxes(session, principal.workspace.id)
    return [_out(i) for i in inboxes]


@router.post(
    "/inboxes",
    response_model=InboxOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CHANNELS_MANAGE))],
)
async def create_inbox(body: InboxCreate, principal: Member, session: Db) -> InboxOut:
    inbox = await inboxes_service.create_inbox(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        name=body.name,
        channel_type=body.channel_type,
        config=body.config,
        secrets=body.secrets,
        enabled=body.enabled,
    )
    return _out(inbox)


@router.get(
    "/inboxes/{inbox_id}",
    response_model=InboxOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def get_inbox(inbox_id: str, principal: Member, session: Db) -> InboxOut:
    inbox = await inboxes_service.get_inbox(session, principal.workspace.id, inbox_id)
    return _out(inbox)


@router.patch(
    "/inboxes/{inbox_id}",
    response_model=InboxOut,
    dependencies=[Depends(require_perm(Perm.CHANNELS_MANAGE))],
)
async def update_inbox(
    inbox_id: str, body: InboxUpdate, principal: Member, session: Db
) -> InboxOut:
    inbox = await inboxes_service.update_inbox(
        session,
        principal.workspace.id,
        inbox_id,
        actor=_actor(principal),
        name=body.name,
        enabled=body.enabled,
        config=body.config,
        secrets=body.secrets,
    )
    return _out(inbox)


@router.delete(
    "/inboxes/{inbox_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.CHANNELS_MANAGE))],
)
async def delete_inbox(inbox_id: str, principal: Member, session: Db) -> Msg:
    await inboxes_service.delete_inbox(
        session, principal.workspace.id, inbox_id, actor=_actor(principal)
    )
    return Msg(message="Inbox deleted")


# ---------------------------------------------------------------------------
# working hours (docs/CHATWOOT-BACKLOG.md §1.1)
# ---------------------------------------------------------------------------


async def _hours_out(session: AsyncSession, workspace_id: str, inbox: Inbox) -> WorkingHoursOut:
    rows = await hours_service.list_hours(session, workspace_id, inbox.id)
    schedule = hours_service.build_schedule(inbox, rows)
    config = inbox.config or {}
    return WorkingHoursOut(
        enabled=bool(config.get("working_hours_enabled", False)),
        timezone=str(config.get("timezone") or "UTC"),
        out_of_office_message=config.get("out_of_office_message"),
        days=[WorkingHourOut.model_validate(r) for r in rows],
        currently_open=is_open(schedule, utcnow()),
    )


@router.get(
    "/inboxes/{inbox_id}/working-hours",
    response_model=WorkingHoursOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def get_working_hours(inbox_id: str, principal: Member, session: Db) -> WorkingHoursOut:
    inbox = await hours_service.get_inbox(session, principal.workspace.id, inbox_id)
    return await _hours_out(session, principal.workspace.id, inbox)


@router.put(
    "/inboxes/{inbox_id}/working-hours",
    response_model=WorkingHoursOut,
    dependencies=[Depends(require_perm(Perm.CHANNELS_MANAGE))],
)
async def set_working_hours(
    inbox_id: str, body: WorkingHoursUpdate, principal: Member, session: Db
) -> WorkingHoursOut:
    """Replace the whole week. `days: []` clears the schedule, which makes the
    inbox always-open regardless of the `enabled` switch."""
    await hours_service.replace_hours(
        session,
        principal.workspace.id,
        inbox_id,
        actor=_actor(principal),
        days=[d.model_dump() for d in body.days],
        enabled=body.enabled,
        timezone=body.timezone,
        out_of_office_message=body.out_of_office_message,
    )
    inbox = await hours_service.get_inbox(session, principal.workspace.id, inbox_id)
    return await _hours_out(session, principal.workspace.id, inbox)
