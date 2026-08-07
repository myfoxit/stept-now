"""Reports / analytics: portable aggregate queries + python medians.

The window is the last ``days`` days. "New" counts conversations created in the
window; "resolved" counts conversations whose resolved_at falls in the window.
Medians are computed in python for dialect portability. AI stats come from
agent G's ``AgentRun`` model, imported defensively and zeroed when unavailable.
"""

from __future__ import annotations

import csv
import importlib
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import ValidationFailure
from app.models.conversation import Conversation, ConversationTag
from app.models.csat import CsatResponse
from app.models.inbox import Inbox
from app.models.sla import AppliedSla, SlaEvent, SlaPolicy, SlaStatus
from app.models.tag import Tag
from app.models.team import Team
from app.models.user import User
from app.schemas.reports import (
    DIMENSIONS,
    ReportBreakdown,
    ReportByAgent,
    ReportByChannel,
    ReportByDay,
    ReportDimensionRow,
    ReportOverview,
    ReportTotals,
    SlaPolicyAttainment,
    SlaReport,
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


# ---------------------------------------------------------------------------
# Dimension breakdowns (docs/CHATWOOT-BACKLOG.md §1.5)
# ---------------------------------------------------------------------------


@dataclass
class _DimConv:
    """A conversation plus the dimension keys it belongs to. `tag_ids` is a list
    because a conversation counts once per tag in the tag breakdown."""

    created_at: datetime
    resolved_at: datetime | None
    first_reply_at: datetime | None
    assignee_user_id: str | None
    team_id: str | None
    inbox_id: str
    channel_type: str
    tag_ids: list[str]


_UNSET_LABELS = {
    "agent": "Unassigned",
    "team": "No team",
    "tag": "Untagged",
}


def _row_filter(dimension: str, key: str) -> dict[str, Any]:
    """The conversation filter that reproduces one breakdown row, so clicking a
    number in a report opens exactly those conversations."""
    field = {
        "agent": "assignee_user_id",
        "team": "team_id",
        "inbox": "inbox_id",
        "tag": "tag_id",
    }.get(dimension)
    if field is None:  # channel has no conversation column — filter by its inboxes
        return {"match": "all", "conditions": []}
    if not key:
        op, value = ("not_exists", None) if dimension != "tag" else ("not_exists", None)
        return {"match": "all", "conditions": [{"field": field, "op": op, "value": value}]}
    op = "in" if dimension == "tag" else "eq"
    return {
        "match": "all",
        "conditions": [{"field": field, "op": op, "value": [key] if op == "in" else key}],
    }


async def _load_dimension_rows(
    session: AsyncSession, workspace_id: str, start: datetime
) -> list[_DimConv]:
    rows = (
        await session.execute(
            select(
                Conversation.id,
                Conversation.created_at,
                Conversation.resolved_at,
                Conversation.first_reply_at,
                Conversation.assignee_user_id,
                Conversation.team_id,
                Conversation.inbox_id,
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
    ids = [row[0] for row in rows]
    tags_by_conversation: dict[str, list[str]] = defaultdict(list)
    if ids:
        tag_rows = await session.execute(
            select(ConversationTag.conversation_id, ConversationTag.tag_id).where(
                ConversationTag.conversation_id.in_(ids)
            )
        )
        for conversation_id, tag_id in tag_rows.all():
            tags_by_conversation[conversation_id].append(tag_id)
    return [
        _DimConv(
            created_at=row.created_at,
            resolved_at=row.resolved_at,
            first_reply_at=row.first_reply_at,
            assignee_user_id=row.assignee_user_id,
            team_id=row.team_id,
            inbox_id=row.inbox_id,
            channel_type=row.channel_type,
            tag_ids=tags_by_conversation.get(row.id, []),
        )
        for row in rows
    ]


def _keys_for(conversation: _DimConv, dimension: str) -> list[str]:
    if dimension == "agent":
        return [conversation.assignee_user_id or ""]
    if dimension == "team":
        return [conversation.team_id or ""]
    if dimension == "inbox":
        return [conversation.inbox_id]
    if dimension == "channel":
        return [conversation.channel_type]
    return conversation.tag_ids or [""]


async def _labels_for(
    session: AsyncSession, workspace_id: str, dimension: str, keys: set[str]
) -> dict[str, str]:
    real = {k for k in keys if k}
    if not real:
        return {}
    if dimension == "agent":
        rows = await session.execute(select(User.id, User.name).where(User.id.in_(real)))
    elif dimension == "team":
        rows = await session.execute(
            select(Team.id, Team.name).where(Team.workspace_id == workspace_id, Team.id.in_(real))
        )
    elif dimension == "inbox":
        rows = await session.execute(
            select(Inbox.id, Inbox.name).where(
                Inbox.workspace_id == workspace_id, Inbox.id.in_(real)
            )
        )
    elif dimension == "tag":
        rows = await session.execute(
            select(Tag.id, Tag.name).where(Tag.workspace_id == workspace_id, Tag.id.in_(real))
        )
    else:  # channel keys are already human-readable
        return {k: k for k in real}
    return {key: name for key, name in rows.all()}


async def breakdown(
    session: AsyncSession, workspace_id: str, *, dimension: str, days: int
) -> ReportBreakdown:
    """Per-dimension volume + speed, each row carrying its drill-down filter."""
    if dimension not in DIMENSIONS:
        raise ValidationFailure(f"dimension must be one of {DIMENSIONS}")
    now = utcnow()
    start = now - timedelta(days=days)
    conversations = await _load_dimension_rows(session, workspace_id, start)

    grouped: dict[str, list[_DimConv]] = defaultdict(list)
    for conversation in conversations:
        for key in _keys_for(conversation, dimension):
            grouped[key].append(conversation)

    labels = await _labels_for(session, workspace_id, dimension, set(grouped))
    rows: list[ReportDimensionRow] = []
    for key, items in grouped.items():
        new = [c for c in items if c.created_at >= start]
        resolved = [c for c in items if c.resolved_at is not None and c.resolved_at >= start]
        frt = [_minutes(c.first_reply_at, c.created_at) for c in new if c.first_reply_at]
        rt = [_minutes(c.resolved_at, c.created_at) for c in resolved if c.resolved_at is not None]
        rows.append(
            ReportDimensionRow(
                key=key,
                label=labels.get(key) or _UNSET_LABELS.get(dimension, "Unknown"),
                new=len(new),
                resolved=len(resolved),
                resolution_rate=_round(len(resolved) / len(new)) if new else 0.0,
                median_first_response_minutes=_round(median(frt)) if frt else None,
                median_resolution_minutes=_round(median(rt)) if rt else None,
                filter=_row_filter(dimension, key),
            )
        )
    rows.sort(key=lambda r: (-r.new, -r.resolved, r.label))
    return ReportBreakdown(dimension=dimension, days=days, rows=rows)


# ---------------------------------------------------------------------------
# SLA attainment — nothing read app.models.sla into a report before this
# ---------------------------------------------------------------------------


async def sla_report(session: AsyncSession, workspace_id: str, *, days: int) -> SlaReport:
    start = utcnow() - timedelta(days=days)
    applied_rows = (
        await session.execute(
            select(AppliedSla, SlaPolicy.name)
            .join(SlaPolicy, SlaPolicy.id == AppliedSla.sla_policy_id)
            .where(
                AppliedSla.workspace_id == workspace_id,
                AppliedSla.created_at >= start,
            )
        )
    ).all()
    breach_rows = (
        await session.execute(
            select(SlaEvent.applied_sla_id, SlaEvent.event_type).where(
                SlaEvent.workspace_id == workspace_id, SlaEvent.created_at >= start
            )
        )
    ).all()
    breaches_by_applied: dict[str, list[str]] = defaultdict(list)
    for applied_id, event_type in breach_rows:
        breaches_by_applied[applied_id].append(event_type)

    buckets: dict[str, dict[str, Any]] = {}
    for applied, policy_name in applied_rows:
        bucket = buckets.setdefault(
            applied.sla_policy_id,
            {
                "name": policy_name,
                "applied": 0,
                "hit": 0,
                "missed": 0,
                "active": 0,
                "frt": 0,
                "nrt": 0,
                "rt": 0,
            },
        )
        bucket["applied"] += 1
        if applied.status == SlaStatus.HIT.value:
            bucket["hit"] += 1
        elif applied.status == SlaStatus.MISSED.value:
            bucket["missed"] += 1
        else:
            bucket["active"] += 1
        for event_type in breaches_by_applied.get(applied.id, []):
            bucket[event_type] = bucket.get(event_type, 0) + 1

    def _attainment(key: str, data: dict[str, Any]) -> SlaPolicyAttainment:
        completed = data["hit"] + data["missed"]
        return SlaPolicyAttainment(
            sla_policy_id=key,
            name=data["name"],
            applied=data["applied"],
            hit=data["hit"],
            missed=data["missed"],
            active=data["active"],
            attainment_rate=_round(data["hit"] / completed) if completed else 0.0,
            frt_breaches=data.get("frt", 0),
            nrt_breaches=data.get("nrt", 0),
            rt_breaches=data.get("rt", 0),
        )

    by_policy = [_attainment(key, data) for key, data in buckets.items()]
    by_policy.sort(key=lambda p: (-p.applied, p.name))
    total = {
        "name": "All policies",
        "applied": sum(p.applied for p in by_policy),
        "hit": sum(p.hit for p in by_policy),
        "missed": sum(p.missed for p in by_policy),
        "active": sum(p.active for p in by_policy),
        "frt": sum(p.frt_breaches for p in by_policy),
        "nrt": sum(p.nrt_breaches for p in by_policy),
        "rt": sum(p.rt_breaches for p in by_policy),
    }
    return SlaReport(days=days, totals=_attainment("", total), by_policy=by_policy)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def breakdown_csv(report: ReportBreakdown) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            report.dimension,
            "new",
            "resolved",
            "resolution_rate",
            "median_first_response_minutes",
            "median_resolution_minutes",
        ]
    )
    for row in report.rows:
        writer.writerow(
            [
                row.label,
                row.new,
                row.resolved,
                row.resolution_rate,
                row.median_first_response_minutes
                if row.median_first_response_minutes is not None
                else "",
                row.median_resolution_minutes if row.median_resolution_minutes is not None else "",
            ]
        )
    return buffer.getvalue()


def sla_csv(report: SlaReport) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "policy",
            "applied",
            "hit",
            "missed",
            "active",
            "attainment_rate",
            "frt_breaches",
            "nrt_breaches",
            "rt_breaches",
        ]
    )
    for row in [*report.by_policy, report.totals]:
        writer.writerow(
            [
                row.name,
                row.applied,
                row.hit,
                row.missed,
                row.active,
                row.attainment_rate,
                row.frt_breaches,
                row.nrt_breaches,
                row.rt_breaches,
            ]
        )
    return buffer.getvalue()


def overview_csv(report: ReportOverview) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "new", "resolved"])
    for day in report.by_day:
        writer.writerow([day.date, day.new, day.resolved])
    return buffer.getvalue()
