"""record_search / record_feedback / analytics_overview math on pinned datasets."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.db import utcnow, uuid7
from app.core.errors import ValidationFailure
from app.models.search_analytics import MessageFeedback, SearchQuery
from app.services import search_analytics as analytics_service
from tests.analytics.conftest import add_query, make_workspace


async def test_record_search_strips_and_truncates(db_only):
    ws = await make_workspace(db_only)
    await analytics_service.record_search(
        db_only,
        ws.id,
        query="  " + "x" * 600,
        source="playground",
        results_count=4,
        top_score=0.5,
        latency_ms=12,
    )
    row = (await db_only.execute(select(SearchQuery))).scalar_one()
    assert row.workspace_id == ws.id
    assert row.query == "x" * 500
    assert row.source == "playground"
    assert (row.results_count, row.top_score, row.latency_ms) == (4, 0.5, 12)
    assert row.created_at is not None


async def test_overview_query_math(db_only):
    ws = await make_workspace(db_only)
    spec = [
        # (query, source, results, top_score, latency, days_ago)
        ("How do I install the widget", "playground", 5, 0.9, 100, 1),
        ("  how do i install the widget ", "widget", 3, 0.7, 50, 2),
        ("warranty policy", "widget", 0, None, 30, 2),
        ("warranty policy", "agent", 0, None, 20, 3),
        ("billing", "playground", 2, 0.5, 200, 0),
        ("out of window", "playground", 9, 0.9, 5, 30),  # excluded (days=7)
    ]
    for query, source, results, score, latency, days_ago in spec:
        await add_query(
            db_only,
            ws.id,
            query=query,
            source=source,
            results_count=results,
            top_score=score,
            latency_ms=latency,
            days_ago=days_ago,
        )

    data = await analytics_service.analytics_overview(db_only, ws.id, days=7)
    q = data["queries"]
    assert q["total"] == 5
    assert q["zero_result_count"] == 2
    assert q["zero_result_rate"] == 0.4
    assert q["avg_top_score"] == round((0.9 + 0.7 + 0.5) / 3, 4)
    assert q["avg_latency_ms"] == 80.0
    assert q["by_source"] == [
        {"source": "playground", "count": 2},
        {"source": "widget", "count": 2},
        {"source": "agent", "count": 1},
    ]

    assert data["top_queries"] == [
        {"query": "how do i install the widget", "count": 2, "avg_top_score": 0.8},
        {"query": "warranty policy", "count": 2, "avg_top_score": None},
        {"query": "billing", "count": 1, "avg_top_score": 0.5},
    ]
    assert data["zero_result_queries"] == [{"query": "warranty policy", "count": 2}]

    # no feedback / no runs in this workspace
    assert data["feedback"] == {"up": 0, "down": 0, "negative_rate": 0.0}
    assert data["ai"] == {"runs": 0, "completed": 0, "handed_off": 0, "deflection_rate": 0.0}


async def test_overview_per_day_covers_window(db_only):
    ws = await make_workspace(db_only)
    await add_query(db_only, ws.id, query="a", days_ago=0)
    await add_query(db_only, ws.id, query="b", days_ago=2)
    await add_query(db_only, ws.id, query="c", days_ago=2)

    data = await analytics_service.analytics_overview(db_only, ws.id, days=7)
    per_day = data["queries"]["per_day"]
    assert len(per_day) == 8  # every day of the window, zeros included
    assert sum(entry["count"] for entry in per_day) == 3
    counts = {entry["date"]: entry["count"] for entry in per_day}
    assert counts[(utcnow() - timedelta(days=2)).date().isoformat()] == 2
    assert counts[utcnow().date().isoformat()] == 1


async def test_overview_days_param_filters_window(db_only):
    ws = await make_workspace(db_only)
    await add_query(db_only, ws.id, query="recent", days_ago=2)
    await add_query(db_only, ws.id, query="older", days_ago=20)

    week = await analytics_service.analytics_overview(db_only, ws.id, days=7)
    month = await analytics_service.analytics_overview(db_only, ws.id, days=30)
    assert week["queries"]["total"] == 1
    assert month["queries"]["total"] == 2


async def test_overview_empty_workspace(db_only):
    ws = await make_workspace(db_only)
    data = await analytics_service.analytics_overview(db_only, ws.id, days=30)
    q = data["queries"]
    assert q["total"] == 0
    assert q["zero_result_rate"] == 0.0
    assert q["avg_top_score"] is None
    assert q["avg_latency_ms"] is None
    assert q["by_source"] == []
    assert data["top_queries"] == []
    assert data["zero_result_queries"] == []


async def test_feedback_upsert_updates_single_row(db_only):
    ws = await make_workspace(db_only)
    conversation_id, message_id, actor_id = uuid7(), uuid7(), uuid7()
    first = await analytics_service.record_feedback(
        db_only,
        ws.id,
        conversation_id=conversation_id,
        message_id=message_id,
        rating="up",
        actor_type="contact",
        actor_id=actor_id,
    )
    second = await analytics_service.record_feedback(
        db_only,
        ws.id,
        conversation_id=conversation_id,
        message_id=message_id,
        rating="down",
        comment="wrong answer",
        actor_type="contact",
        actor_id=actor_id,
    )
    assert second.id == first.id
    rows = (await db_only.execute(select(MessageFeedback))).scalars().all()
    assert len(rows) == 1
    assert rows[0].rating == "down"
    assert rows[0].comment == "wrong answer"


async def test_feedback_distinct_actors_get_distinct_rows(db_only):
    ws = await make_workspace(db_only)
    conversation_id, message_id = uuid7(), uuid7()
    await analytics_service.record_feedback(
        db_only,
        ws.id,
        conversation_id=conversation_id,
        message_id=message_id,
        rating="up",
        actor_type="contact",
        actor_id=uuid7(),
    )
    await analytics_service.record_feedback(
        db_only,
        ws.id,
        conversation_id=conversation_id,
        message_id=message_id,
        rating="down",
        actor_type="user",
        actor_id=uuid7(),
    )
    rows = (await db_only.execute(select(MessageFeedback))).scalars().all()
    assert len(rows) == 2


async def test_feedback_invalid_rating_rejected(db_only):
    ws = await make_workspace(db_only)
    with pytest.raises(ValidationFailure):
        await analytics_service.record_feedback(
            db_only,
            ws.id,
            conversation_id=uuid7(),
            message_id=uuid7(),
            rating="meh",
            actor_type="contact",
            actor_id=uuid7(),
        )


async def test_feedback_emits_event(db_only, monkeypatch):
    captured = []

    async def _capture(session, event):
        captured.append(event)

    monkeypatch.setattr(analytics_service, "emit", _capture)
    ws = await make_workspace(db_only)
    conversation_id, message_id = uuid7(), uuid7()
    await analytics_service.record_feedback(
        db_only,
        ws.id,
        conversation_id=conversation_id,
        message_id=message_id,
        rating="down",
        actor_type="contact",
        actor_id=uuid7(),
    )
    (event,) = captured
    assert event.name == "message.feedback"
    assert event.workspace_id == ws.id
    assert event.payload == {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "rating": "down",
        "actor_type": "contact",
    }


async def test_overview_counts_feedback_in_window(db_only):
    ws = await make_workspace(db_only)
    for rating, actor in (("up", uuid7()), ("down", uuid7()), ("down", uuid7())):
        await analytics_service.record_feedback(
            db_only,
            ws.id,
            conversation_id=uuid7(),
            message_id=uuid7(),
            rating=rating,
            actor_type="contact",
            actor_id=actor,
        )
    data = await analytics_service.analytics_overview(db_only, ws.id, days=7)
    assert data["feedback"] == {"up": 1, "down": 2, "negative_rate": round(2 / 3, 4)}


async def test_overview_ai_stats_from_agent_runs(db_only):
    from app.models.agent import Agent
    from app.models.agent_run import AgentRun

    ws = await make_workspace(db_only)
    agent = Agent(workspace_id=ws.id, name="Fin")
    db_only.add(agent)
    await db_only.flush()

    def run(status: str, days_ago: int = 1) -> AgentRun:
        return AgentRun(
            workspace_id=ws.id,
            conversation_id=uuid7(),
            agent_id=agent.id,
            status=status,
            created_at=utcnow() - timedelta(days=days_ago),
        )

    db_only.add_all(
        [
            run("completed"),
            run("completed"),
            run("handed_off"),
            run("failed"),
            run("completed", days_ago=40),  # outside the 30-day window
        ]
    )
    await db_only.flush()

    data = await analytics_service.analytics_overview(db_only, ws.id, days=30)
    assert data["ai"] == {"runs": 4, "completed": 2, "handed_off": 1, "deflection_rate": 0.5}
