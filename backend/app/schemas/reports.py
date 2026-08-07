"""Reports / analytics schemas (see docs/CONTRACTS.md)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ReportTotals(BaseModel):
    new_conversations: int
    resolved_conversations: int
    resolution_rate: float
    median_first_response_minutes: float | None = None
    median_resolution_minutes: float | None = None
    csat_avg: float | None = None
    csat_count: int
    ai_runs: int
    ai_resolved: int
    ai_resolution_rate: float


class ReportByDay(BaseModel):
    date: str  # YYYY-MM-DD
    new: int
    resolved: int


class ReportByChannel(BaseModel):
    channel_type: str
    count: int


class ReportByAgent(BaseModel):
    user_id: str
    name: str
    resolved: int
    median_first_response_minutes: float | None = None


class ReportOverview(BaseModel):
    totals: ReportTotals
    by_day: list[ReportByDay]
    by_channel: list[ReportByChannel]
    by_agent: list[ReportByAgent]


# ---------------------------------------------------------------------------
# Dimension breakdowns + SLA attainment (docs/CHATWOOT-BACKLOG.md §1.5)
# ---------------------------------------------------------------------------

DIMENSIONS = ("agent", "team", "inbox", "tag", "channel")


class ReportDimensionRow(BaseModel):
    """One row of a breakdown. `filter` is a conversation filter document that
    reproduces exactly this row's population — the drill-down payload."""

    key: str  # id, or "" for the unassigned/untagged bucket
    label: str
    new: int
    resolved: int
    resolution_rate: float
    median_first_response_minutes: float | None = None
    median_resolution_minutes: float | None = None
    filter: dict[str, Any]


class ReportBreakdown(BaseModel):
    dimension: str
    days: int
    rows: list[ReportDimensionRow]


class SlaPolicyAttainment(BaseModel):
    sla_policy_id: str
    name: str
    applied: int
    hit: int
    missed: int
    active: int
    attainment_rate: float  # hit / (hit + missed), 0 when nothing has completed
    frt_breaches: int
    nrt_breaches: int
    rt_breaches: int


class SlaReport(BaseModel):
    days: int
    totals: SlaPolicyAttainment
    by_policy: list[SlaPolicyAttainment]
