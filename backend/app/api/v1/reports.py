"""Reports API: workspace analytics overview.

The empty router is pre-registered; add routes here, never touch the registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.deps import Db, Member, require_perm
from app.core.errors import ValidationFailure
from app.core.permissions import Perm
from app.schemas.reports import ReportOverview
from app.services import reports as reports_service

router = APIRouter()

_ALLOWED_DAYS = (7, 30, 90)


@router.get(
    "/reports/overview",
    response_model=ReportOverview,
    dependencies=[Depends(require_perm(Perm.REPORTS_READ))],
)
async def reports_overview(principal: Member, session: Db, days: int = Query(7)) -> ReportOverview:
    if days not in _ALLOWED_DAYS:
        raise ValidationFailure(f"days must be one of {_ALLOWED_DAYS}")
    return await reports_service.overview(session, principal.workspace.id, days=days)
