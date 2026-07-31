"""Search analytics: query logging (fire-and-forget), answer feedback, overview math.

Aggregation happens in python over the window's rows (reports.py style) so the
same code runs on Postgres and SQLite. AI-run stats come from agent G's
``AgentRun`` model, imported defensively and zeroed when unavailable.
"""

from __future__ import annotations

import importlib
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import ValidationFailure
from app.core.events import Actor, Event, EventNames, emit
from app.models.search_analytics import MessageFeedback, SearchQuery

QUERY_MAX_LENGTH = 500
SOURCE_MAX_LENGTH = 20
RATINGS = ("up", "down")
TOP_QUERIES_LIMIT = 10


async def record_search(
    session: AsyncSession,
    workspace_id: str,
    *,
    query: str,
    source: str,
    results_count: int,
    top_score: float | None = None,
    latency_ms: int = 0,
) -> None:
    """Fire-and-forget search log row — no read-back, callers ignore the result."""
    session.add(
        SearchQuery(
            workspace_id=workspace_id,
            query=query.strip()[:QUERY_MAX_LENGTH],
            source=source[:SOURCE_MAX_LENGTH],
            results_count=results_count,
            top_score=top_score,
            latency_ms=latency_ms,
        )
    )
    await session.flush()


async def record_feedback(
    session: AsyncSession,
    workspace_id: str,
    *,
    conversation_id: str,
    message_id: str,
    rating: str,
    comment: str | None = None,
    actor_type: str,
    actor_id: str | None,
) -> MessageFeedback:
    """Upsert one actor's thumbs rating for a message. Emits `message.feedback`."""
    if rating not in RATINGS:
        raise ValidationFailure("rating must be 'up' or 'down'")

    feedback = (
        await session.execute(
            select(MessageFeedback).where(
                MessageFeedback.workspace_id == workspace_id,
                MessageFeedback.message_id == message_id,
                MessageFeedback.actor_type == actor_type,
                MessageFeedback.actor_id == actor_id,
            )
        )
    ).scalar_one_or_none()
    if feedback is None:
        feedback = MessageFeedback(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=message_id,
            actor_type=actor_type,
            actor_id=actor_id,
            rating=rating,
            comment=comment,
        )
        session.add(feedback)
    else:
        feedback.rating = rating
        feedback.comment = comment
    await session.flush()
    await emit(
        session,
        Event(
            name=EventNames.MESSAGE_FEEDBACK,
            workspace_id=workspace_id,
            payload={
                "conversation_id": conversation_id,
                "message_id": message_id,
                "rating": rating,
                "actor_type": actor_type,
            },
            actor=Actor(type=actor_type, id=actor_id),
        ),
    )
    return feedback


# --- overview ---------------------------------------------------------------


def _round(value: float, digits: int = 4) -> float:
    return round(value, digits)


def _normalize(query: str) -> str:
    return " ".join(query.lower().split())


async def analytics_overview(
    session: AsyncSession, workspace_id: str, *, days: int = 30
) -> dict[str, Any]:
    now = utcnow()
    start = now - timedelta(days=days)

    rows = (
        await session.execute(
            select(
                SearchQuery.query,
                SearchQuery.results_count,
                SearchQuery.top_score,
                SearchQuery.latency_ms,
                SearchQuery.source,
                SearchQuery.created_at,
            ).where(
                SearchQuery.workspace_id == workspace_id,
                SearchQuery.created_at >= start,
            )
        )
    ).all()

    total = len(rows)
    zero_count = 0
    top_scores: list[float] = []
    latencies: list[int] = []
    per_day: dict[date, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    by_query: dict[str, list[float]] = defaultdict(list)  # normalized → top_scores
    query_counts: dict[str, int] = defaultdict(int)
    zero_query_counts: dict[str, int] = defaultdict(int)

    for query, results_count, top_score, latency_ms, source, created_at in rows:
        normalized = _normalize(query)
        per_day[created_at.date()] += 1
        by_source[source] += 1
        query_counts[normalized] += 1
        latencies.append(latency_ms)
        if top_score is not None:
            top_scores.append(top_score)
            by_query[normalized].append(top_score)
        if results_count == 0:
            zero_count += 1
            zero_query_counts[normalized] += 1

    per_day_out: list[dict[str, Any]] = []
    day = start.date()
    while day <= now.date():
        per_day_out.append({"date": day.isoformat(), "count": per_day[day]})
        day += timedelta(days=1)

    def _top(counts: dict[str, int]) -> list[tuple[str, int]]:
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_QUERIES_LIMIT]

    top_queries = [
        {
            "query": query,
            "count": count,
            "avg_top_score": (
                _round(sum(by_query[query]) / len(by_query[query])) if by_query[query] else None
            ),
        }
        for query, count in _top(query_counts)
    ]
    zero_result_queries = [
        {"query": query, "count": count} for query, count in _top(zero_query_counts)
    ]

    up, down = await _feedback_counts(session, workspace_id, start)

    return {
        "queries": {
            "total": total,
            "per_day": per_day_out,
            "zero_result_count": zero_count,
            "zero_result_rate": _round(zero_count / total) if total else 0.0,
            "avg_top_score": _round(sum(top_scores) / len(top_scores)) if top_scores else None,
            "avg_latency_ms": _round(sum(latencies) / len(latencies)) if latencies else None,
            "by_source": [
                {"source": source, "count": count}
                for source, count in sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        },
        "top_queries": top_queries,
        "zero_result_queries": zero_result_queries,
        "feedback": {
            "up": up,
            "down": down,
            "negative_rate": _round(down / (up + down)) if (up + down) else 0.0,
        },
        "ai": await _ai_stats(session, workspace_id, start),
    }


async def _feedback_counts(session: AsyncSession, workspace_id: str, start: Any) -> tuple[int, int]:
    rows = (
        await session.execute(
            select(MessageFeedback.rating, func.count())
            .where(
                MessageFeedback.workspace_id == workspace_id,
                MessageFeedback.created_at >= start,
            )
            .group_by(MessageFeedback.rating)
        )
    ).all()
    counts = {rating: int(count) for rating, count in rows}
    return counts.get("up", 0), counts.get("down", 0)


def _agent_run_model() -> Any | None:
    """Agent G owns AgentRun and may not have shipped it — resolve dynamically."""
    try:
        module = importlib.import_module("app.models.agent_run")
    except Exception:  # pragma: no cover — defensive
        return None
    return getattr(module, "AgentRun", None)


async def _ai_stats(session: AsyncSession, workspace_id: str, start: Any) -> dict[str, Any]:
    zeroed = {"runs": 0, "completed": 0, "handed_off": 0, "deflection_rate": 0.0}
    agent_run = _agent_run_model()
    if agent_run is None:
        return zeroed
    try:
        rows = (
            await session.execute(
                select(agent_run.status, func.count())
                .where(
                    agent_run.workspace_id == workspace_id,
                    agent_run.created_at >= start,
                )
                .group_by(agent_run.status)
            )
        ).all()
    except Exception:  # pragma: no cover — model exists but query shape differs
        return zeroed
    counts = {status: int(count) for status, count in rows}
    runs = sum(counts.values())
    completed = counts.get("completed", 0)
    return {
        "runs": runs,
        "completed": completed,
        "handed_off": counts.get("handed_off", 0),
        "deflection_rate": _round(completed / runs) if runs else 0.0,
    }
