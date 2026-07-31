"""Reports overview: metric math on seeded fixtures, plus API shape and authz."""

from __future__ import annotations

from datetime import timedelta

from app.core.db import get_session_factory, utcnow
from app.models.workspace import Workspace
from app.services import reports as reports_service
from tests.conftest import bearer, signup
from tests.reports.conftest import (
    build_six_conversations,
    make_contact,
    make_conversation,
    make_inbox,
    make_workspace,
)


async def test_overview_metrics(db_only):
    ws = await make_workspace(db_only)
    await build_six_conversations(db_only, ws)

    ov = await reports_service.overview(db_only, ws.id, days=7)
    t = ov.totals

    assert t.new_conversations == 6
    assert t.resolved_conversations == 4
    assert t.resolution_rate == round(4 / 6, 2)  # 0.67
    assert t.median_first_response_minutes == 30.0  # [10,20,30,40,50]
    assert t.median_resolution_minutes == 150.0  # [60,120,180,200]
    assert t.csat_avg == 4.5
    assert t.csat_count == 2
    # AgentRun model is a stub during this wave → AI stats zeroed.
    assert (t.ai_runs, t.ai_resolved, t.ai_resolution_rate) == (0, 0, 0.0)

    channels = {c.channel_type: c.count for c in ov.by_channel}
    assert channels == {"widget": 3, "email": 2, "api": 1}

    agents = {a.name: a for a in ov.by_agent}
    assert agents["Alice"].resolved == 3
    assert agents["Alice"].median_first_response_minutes == 20.0  # [10,20,50]
    assert agents["Bob"].resolved == 1
    assert agents["Bob"].median_first_response_minutes == 30.0

    assert sum(d.new for d in ov.by_day) == 6
    assert sum(d.resolved for d in ov.by_day) == 4


async def test_overview_empty_workspace(db_only):
    ws = await make_workspace(db_only)
    ov = await reports_service.overview(db_only, ws.id, days=30)
    t = ov.totals
    assert t.new_conversations == 0
    assert t.resolved_conversations == 0
    assert t.resolution_rate == 0.0
    assert t.median_first_response_minutes is None
    assert t.median_resolution_minutes is None
    assert t.csat_avg is None
    assert t.csat_count == 0
    assert ov.by_agent == []
    assert ov.by_channel == []


async def test_window_excludes_out_of_range(db_only):
    ws = await make_workspace(db_only)
    inbox = await make_inbox(db_only, ws)
    contact = await make_contact(db_only, ws)
    now = utcnow()
    await make_conversation(
        db_only,
        ws,
        inbox,
        contact,
        number=1,
        created_at=now - timedelta(days=40),
        resolved_at=now - timedelta(days=40) + timedelta(minutes=10),
    )
    ov = await reports_service.overview(db_only, ws.id, days=7)
    assert ov.totals.new_conversations == 0
    assert ov.totals.resolved_conversations == 0


async def test_resolved_before_window_counts_when_resolved_in_window(db_only):
    ws = await make_workspace(db_only)
    inbox = await make_inbox(db_only, ws)
    contact = await make_contact(db_only, ws)
    now = utcnow()
    # created 10 days ago (outside 7-day window) but resolved 1 day ago (inside)
    await make_conversation(
        db_only,
        ws,
        inbox,
        contact,
        number=1,
        created_at=now - timedelta(days=10),
        resolved_at=now - timedelta(days=1),
    )
    ov = await reports_service.overview(db_only, ws.id, days=7)
    assert ov.totals.new_conversations == 0  # not created in window
    assert ov.totals.resolved_conversations == 1  # resolved in window


async def test_overview_api_and_authz(client, workspace_ctx):
    async with get_session_factory()() as session:
        ws = await session.get(Workspace, workspace_ctx.id)
        assert ws is not None
        await build_six_conversations(session, ws)
        await session.commit()

    resp = await client.get(
        f"{workspace_ctx.base}/reports/overview?days=7", headers=workspace_ctx.owner_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"]["new_conversations"] == 6
    assert body["totals"]["resolved_conversations"] == 4
    assert body["totals"]["csat_count"] == 2
    assert len(body["by_channel"]) == 3

    # invalid window → 422
    bad = await client.get(
        f"{workspace_ctx.base}/reports/overview?days=5", headers=workspace_ctx.owner_headers
    )
    assert bad.status_code == 422

    # cross-workspace principal → 403
    intruder = await signup(client, "intruder-reports@example.com")
    await client.post("/api/v1/workspaces", json={"name": "Theirs"}, headers=bearer(intruder))
    denied = await client.get(
        f"{workspace_ctx.base}/reports/overview?days=7", headers=bearer(intruder)
    )
    assert denied.status_code == 403
