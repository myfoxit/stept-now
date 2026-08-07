"""Audit log + member notifications endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.v1.billing import require_plan_feature
from app.core.deps import Db, Member, require_perm
from app.core.errors import ForbiddenError
from app.core.pagination import OffsetPage, clamp_limit
from app.core.permissions import Perm
from app.schemas.audit import AuditLogOut, NotificationOut
from app.schemas.common import Msg
from app.services import audit as audit_service
from app.services import notifications as notification_service
from app.services.billing import Feature

router = APIRouter()


@router.get(
    "/audit",
    response_model=OffsetPage[AuditLogOut],
    dependencies=[
        Depends(require_perm(Perm.AUDIT_READ)),
        Depends(require_plan_feature(Feature.AUDIT_LOG)),
    ],
)
async def list_audit(
    principal: Member,
    session: Db,
    action: str | None = None,
    actor_id: str | None = None,
    limit: int | None = Query(None, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    limit = clamp_limit(limit, default=50)
    entries, total = await audit_service.list_entries(
        session,
        principal.workspace.id,
        action=action,
        actor_id=actor_id,
        limit=limit,
        offset=offset,
    )
    return OffsetPage(
        items=[AuditLogOut.model_validate(e) for e in entries],
        total=total,
        limit=limit,
        offset=offset,
    )


def _require_user(principal) -> str:
    if principal.user is None:
        raise ForbiddenError("Notifications are user-scoped")
    return principal.user.id


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(principal: Member, session: Db, unread_only: bool = Query(False)):
    user_id = _require_user(principal)
    items = await notification_service.list_for_user(
        session, principal.workspace.id, user_id, unread_only=unread_only
    )
    return [NotificationOut.model_validate(n) for n in items]


@router.post("/notifications/{notification_id}/read", response_model=Msg)
async def mark_notification_read(notification_id: str, principal: Member, session: Db):
    user_id = _require_user(principal)
    await notification_service.mark_read(session, principal.workspace.id, user_id, notification_id)
    return Msg(message="Marked read")


@router.post("/notifications/read-all", response_model=Msg)
async def mark_all_notifications_read(principal: Member, session: Db):
    user_id = _require_user(principal)
    await notification_service.mark_all_read(session, principal.workspace.id, user_id)
    return Msg(message="All marked read")
