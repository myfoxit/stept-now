"""Reports / analytics schemas (see docs/CONTRACTS.md)."""

from __future__ import annotations

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
