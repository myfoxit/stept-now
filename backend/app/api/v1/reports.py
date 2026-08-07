"""Reports API: overview, per-dimension breakdowns, SLA attainment, CSV export.

Every breakdown row carries a `filter` document that reproduces exactly that
row's population, so the UI can turn any number into a conversation list
(`POST /conversations/search`) — that is the drill-down mechanism.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core.deps import Db, Member, require_perm
from app.core.errors import ValidationFailure
from app.core.permissions import Perm
from app.schemas.reports import DIMENSIONS, ReportBreakdown, ReportOverview, SlaReport
from app.services import reports as reports_service

router = APIRouter()

_ALLOWED_DAYS = (7, 30, 90)


def _check_days(days: int) -> int:
    if days not in _ALLOWED_DAYS:
        raise ValidationFailure(f"days must be one of {_ALLOWED_DAYS}")
    return days


def _csv(body: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/reports/overview",
    response_model=ReportOverview,
    dependencies=[Depends(require_perm(Perm.REPORTS_READ))],
)
async def reports_overview(principal: Member, session: Db, days: int = Query(7)) -> ReportOverview:
    return await reports_service.overview(session, principal.workspace.id, days=_check_days(days))


@router.get(
    "/reports/overview.csv",
    dependencies=[Depends(require_perm(Perm.REPORTS_READ))],
)
async def reports_overview_csv(principal: Member, session: Db, days: int = Query(7)) -> Response:
    report = await reports_service.overview(session, principal.workspace.id, days=_check_days(days))
    return _csv(reports_service.overview_csv(report), f"conversations-{days}d.csv")


@router.get(
    "/reports/breakdown",
    response_model=ReportBreakdown,
    dependencies=[Depends(require_perm(Perm.REPORTS_READ))],
)
async def reports_breakdown(
    principal: Member,
    session: Db,
    dimension: str = Query("agent", description=f"one of {DIMENSIONS}"),
    days: int = Query(7),
) -> ReportBreakdown:
    """Volume + speed grouped by agent, team, inbox, tag or channel."""
    return await reports_service.breakdown(
        session, principal.workspace.id, dimension=dimension, days=_check_days(days)
    )


@router.get(
    "/reports/breakdown.csv",
    dependencies=[Depends(require_perm(Perm.REPORTS_READ))],
)
async def reports_breakdown_csv(
    principal: Member,
    session: Db,
    dimension: str = Query("agent"),
    days: int = Query(7),
) -> Response:
    report = await reports_service.breakdown(
        session, principal.workspace.id, dimension=dimension, days=_check_days(days)
    )
    return _csv(reports_service.breakdown_csv(report), f"{dimension}-{days}d.csv")


@router.get(
    "/reports/sla",
    response_model=SlaReport,
    dependencies=[Depends(require_perm(Perm.REPORTS_READ))],
)
async def reports_sla(principal: Member, session: Db, days: int = Query(30)) -> SlaReport:
    """SLA attainment per policy, plus a breakdown of which target was missed."""
    return await reports_service.sla_report(session, principal.workspace.id, days=_check_days(days))


@router.get(
    "/reports/sla.csv",
    dependencies=[Depends(require_perm(Perm.REPORTS_READ))],
)
async def reports_sla_csv(principal: Member, session: Db, days: int = Query(30)) -> Response:
    report = await reports_service.sla_report(
        session, principal.workspace.id, days=_check_days(days)
    )
    return _csv(reports_service.sla_csv(report), f"sla-{days}d.csv")
