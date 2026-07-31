"""GET /knowledge/analytics: shape, days validation, workspace access."""

from __future__ import annotations

from app.core.db import get_session_factory
from tests.analytics.conftest import add_query
from tests.conftest import bearer, signup


async def test_analytics_overview_endpoint(client, workspace_ctx):
    async with get_session_factory()() as session:
        await add_query(
            session,
            workspace_ctx.id,
            query="install widget",
            source="playground",
            results_count=4,
            top_score=0.8,
            latency_ms=40,
            days_ago=1,
        )
        await add_query(
            session, workspace_ctx.id, query="warranty", source="widget", results_count=0
        )
        await session.commit()

    response = await client.get(
        f"{workspace_ctx.base}/knowledge/analytics", headers=workspace_ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queries"]["total"] == 2
    assert body["queries"]["zero_result_count"] == 1
    assert body["queries"]["zero_result_rate"] == 0.5
    assert {entry["source"] for entry in body["queries"]["by_source"]} == {"playground", "widget"}
    assert body["zero_result_queries"] == [{"query": "warranty", "count": 1}]
    assert body["feedback"] == {"up": 0, "down": 0, "negative_rate": 0.0}
    assert body["ai"] == {"runs": 0, "completed": 0, "handed_off": 0, "deflection_rate": 0.0}


async def test_analytics_days_validation(client, workspace_ctx):
    ok = await client.get(
        f"{workspace_ctx.base}/knowledge/analytics?days=7", headers=workspace_ctx.owner_headers
    )
    assert ok.status_code == 200
    bad = await client.get(
        f"{workspace_ctx.base}/knowledge/analytics?days=13", headers=workspace_ctx.owner_headers
    )
    assert bad.status_code == 422


async def test_analytics_requires_membership(client, workspace_ctx):
    outsider = await signup(client, "outsider-analytics@example.com")
    response = await client.get(
        f"{workspace_ctx.base}/knowledge/analytics", headers=bearer(outsider)
    )
    assert response.status_code == 403
