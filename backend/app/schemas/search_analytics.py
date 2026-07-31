"""Pydantic schemas for search analytics + message feedback."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Rating = Literal["up", "down"]


class FeedbackCreate(BaseModel):
    rating: Rating
    comment: str | None = Field(default=None, max_length=5000)


class MessageFeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    message_id: str
    actor_type: str
    actor_id: str | None = None
    rating: str
    comment: str | None = None
    created_at: datetime


# --- analytics overview -----------------------------------------------------


class QueriesPerDay(BaseModel):
    date: str  # YYYY-MM-DD
    count: int


class QueriesBySource(BaseModel):
    source: str
    count: int


class QueryStats(BaseModel):
    total: int
    per_day: list[QueriesPerDay]
    zero_result_count: int
    zero_result_rate: float
    avg_top_score: float | None = None
    avg_latency_ms: float | None = None
    by_source: list[QueriesBySource]


class TopQuery(BaseModel):
    query: str
    count: int
    avg_top_score: float | None = None


class ZeroResultQuery(BaseModel):
    query: str
    count: int


class FeedbackStats(BaseModel):
    up: int
    down: int
    negative_rate: float


class AiStats(BaseModel):
    runs: int
    completed: int
    handed_off: int
    deflection_rate: float


class SearchAnalyticsOverview(BaseModel):
    queries: QueryStats
    top_queries: list[TopQuery]
    zero_result_queries: list[ZeroResultQuery]
    feedback: FeedbackStats
    ai: AiStats
