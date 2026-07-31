"""Reports / analytics: portable aggregate queries + python medians.

The window is the last ``days`` days. "New" counts conversations created in the
window; "resolved" counts conversations whose resolved_at falls in the window.
Medians are computed in python for dialect portability. AI stats come from
agent G's ``AgentRun`` model, imported defensively and zeroed when unavailable.
"""

from __future__ import annotations

import importlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.conversation import Conversation
from app.models.csat import CsatResponse
from app.models.inbox import Inbox
from app.models.user import User
from app.schemas.reports import (
    ReportByAgent,
    ReportByChannel,
    ReportByDay,
    ReportOverview,
    ReportTotals,
)


@dataclass
class _Conv:
    created_at: datetime
    resolved_at: datetime | None
    first_reply_at: datetime | None
    assignee_user_id: str | None
    channel_type: str


def _minutes(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 60.0


def _round(value: float, digits: int = 2) -> float:
    return round(value, digits)


async def overview(session: AsyncSession, workspace_id: str, *, days: int) -> ReportOverview:
    now = utcnow()
    start = now - timedelta(days=days)

    rows = (
        await session.execute(
            select(
                Conversation.created_at,
                Conversation.resolved_at,
                Conversation.first_reply_at,
                Conversation.assignee_user_id,
                Inbox.channel_type,
            )
            .join(Inbox, Inbox.id == Conversation.inbox_id)
            .where(
                Conversation.workspace_id == workspace_id,
                (Conversation.created_at >= start)
                | (Conversation.resolved_at.is_not(None) & (Conversation.resolved_at >= start)),
            )
        )
    ).all()
    convs = [_Conv(*row) for row in rows]

    new = [c for c in convs if c.created_at >= start]
    resolved = [c for c in convs if c.resolved_at is not None and c.resolved_at >= start]

    first_response = [_minutes(c.first_reply_at, c.created_at) for c in new if c.first_reply_at]
    resolution_times = [
        _minutes(c.resolved_at, c.created_at) for c in resolved if c.resolved_at is not None
    ]

    csat_avg, csat_count = await _csat(session, workspace_id, start)
    ai_runs, ai_resolved = await _ai_stats(session, workspace_id, start)

    totals = ReportTotals(
        new_conversations=len(new),
        resolved_conversations=len(resolved),
        resolution_rate=_round(len(resolved) / len(new)) if new else 0.0,
        median_first_response_minutes=_round(median(first_response)) if first_response else None,
        median_resolution_minutes=_round(median(resolution_times)) if resolution_times else None,
        csat_avg=csat_avg,
        csat_count=csat_count,
        ai_runs=ai_runs,
        ai_resolved=ai_resolved,
        ai_resolution_rate=_round(ai_resolved / ai_runs) if ai_runs else 0.0,
    )

    return ReportOverview(
        totals=totals,
        by_day=_by_day(new, resolved, start.date(), now.date()),
        by_channel=_by_channel(new),
        by_agent=await _by_agent(session, resolved),
    )


def _by_day(new: list[_Conv], resolved: list[_Conv], start: date, end: date) -> list[ReportByDay]:
    new_counts: dict[date, int] = defaultdict(int)
    resolved_counts: dict[date, int] = defaultdict(int)
    for c in new:
        new_counts[c.created_at.date()] += 1
    for c in resolved:
        if c.resolved_at is not None:
            resolved_counts[c.resolved_at.date()] += 1
    out: list[ReportByDay] = []
    day = start
    while day <= end:
        out.append(
            ReportByDay(date=day.isoformat(), new=new_counts[day], resolved=resolved_counts[day])
        )
        day += timedelta(days=1)
    return out


def _by_channel(new: list[_Conv]) -> list[ReportByChannel]:
    counts: dict[str, int] = defaultdict(int)
    for c in new:
        counts[c.channel_type] += 1
    return [
        ReportByChannel(channel_type=channel, count=count)
        for channel, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


async def _by_agent(session: AsyncSession, resolved: list[_Conv]) -> list[ReportByAgent]:
    by_user: dict[str, list[_Conv]] = defaultdict(list)
    for c in resolved:
        if c.assignee_user_id:
            by_user[c.assignee_user_id].append(c)
    if not by_user:
        return []
    name_rows = await session.execute(
        select(User.id, User.name).where(User.id.in_(list(by_user.keys())))
    )
    names = {user_id: name for user_id, name in name_rows.all()}
    out: list[ReportByAgent] = []
    for user_id, cs in by_user.items():
        frt = [_minutes(c.first_reply_at, c.created_at) for c in cs if c.first_reply_at]
        out.append(
            ReportByAgent(
                user_id=user_id,
                name=names.get(user_id, "Unknown"),
                resolved=len(cs),
                median_first_response_minutes=_round(median(frt)) if frt else None,
            )
        )
    out.sort(key=lambda a: (-a.resolved, a.name))
    return out


async def _csat(
    session: AsyncSession, workspace_id: str, start: datetime
) -> tuple[float | None, int]:
    row = (
        await session.execute(
            select(func.avg(CsatResponse.rating), func.count()).where(
                CsatResponse.workspace_id == workspace_id,
                CsatResponse.created_at >= start,
            )
        )
    ).one()
    avg, count = row
    return (_round(float(avg)) if avg is not None else None), int(count or 0)


def _agent_run_model() -> Any | None:
    """Agent G owns AgentRun and may not have shipped it — resolve dynamically."""
    try:
        module = importlib.import_module("app.models.agent_run")
    except Exception:  # pragma: no cover — defensive
        return None
    return getattr(module, "AgentRun", None)


async def _ai_stats(session: AsyncSession, workspace_id: str, start: datetime) -> tuple[int, int]:
    agent_run = _agent_run_model()
    if agent_run is None:
        return 0, 0
    try:
        runs = (
            await session.execute(
                select(func.count()).where(
                    agent_run.workspace_id == workspace_id,
                    agent_run.created_at >= start,
                )
            )
        ).scalar_one()
        resolved = (
            await session.execute(
                select(func.count()).where(
                    agent_run.workspace_id == workspace_id,
                    agent_run.created_at >= start,
                    agent_run.status == "completed",
                )
            )
        ).scalar_one()
        return int(runs), int(resolved)
    except Exception:  # pragma: no cover — model exists but query shape differs
        return 0, 0
