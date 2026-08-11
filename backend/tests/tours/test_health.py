"""Playback health rollup: `step_blocked` is a first-class stuck-tour signal.

New lifecycle event (visitor pressed Next, the next step's anchor was missing on
the page): it flows into stats and makes a tour at least "yellow" — a tour
people cannot finish is broken for them even when every rendered step resolved.
Dismissals stay neutral: closing a tour is a choice, not breakage.
"""

from __future__ import annotations

from app.core.db import get_session_factory
from app.models.tour import Tour
from app.services import tours as tours_service
from tests.tours.conftest import (
    create_tour,
    insert_events,
    publish_tour,
    widget_key_for,
)


async def _health(workspace_id: str, tour_id: str) -> dict:
    async with get_session_factory()() as session:
        tour = await session.get(Tour, tour_id)
        assert tour is not None
        return await tours_service.tour_health(session, workspace_id, tour)


async def _live_tour(client, ctx) -> dict:
    tour = await create_tour(client, ctx)
    await publish_tour(client, ctx, tour["id"])
    return tour


async def test_step_blocked_flows_into_stats(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    tour = await _live_tour(client, workspace_ctx)
    for event, index in (("started", None), ("step_viewed", 0), ("step_blocked", 1)):
        resp = await client.post(
            f"/api/widget/tours/{tour['id']}/events?widget_key={key}",
            json={"event": event, **({"step_index": index} if index is not None else {})},
        )
        assert resp.status_code == 200, resp.text

    stats = await client.get(
        f"{workspace_ctx.base}/tours/{tour['id']}/stats", headers=workspace_ctx.owner_headers
    )
    assert stats.status_code == 200, stats.text
    assert stats.json()["step_blocked"] == 1


async def test_dismissal_is_not_breakage(client, workspace_ctx):
    tour = await _live_tour(client, workspace_ctx)
    await insert_events(
        workspace_ctx.id,
        tour["id"],
        [(None, "started", None), (None, "step_viewed", 0), (None, "dismissed", 0)],
    )
    health = await _health(workspace_ctx.id, tour["id"])
    assert health["health"] == "green"


async def test_step_blocked_makes_a_tour_at_least_yellow(client, workspace_ctx):
    tour = await _live_tour(client, workspace_ctx)
    await insert_events(
        workspace_ctx.id,
        tour["id"],
        [(None, "started", None), (None, "step_blocked", 1), (None, "step_blocked", 1)],
    )
    health = await _health(workspace_ctx.id, tour["id"])
    assert health["health"] == "yellow"
    assert health["step_blocked"] == 2
    assert health["blocked_steps"] == [{"index": 1, "title": "AI", "count": 2}]


async def test_step_error_stays_red_even_with_blocked(client, workspace_ctx):
    tour = await _live_tour(client, workspace_ctx)
    await insert_events(
        workspace_ctx.id,
        tour["id"],
        [
            (None, "started", None),
            (None, "step_blocked", 1),
            (None, "step_error", 0, {"reason": "not_found"}),
        ],
    )
    health = await _health(workspace_ctx.id, tour["id"])
    assert health["health"] == "red"
    assert health["step_errors"] == 1
    assert health["step_blocked"] == 1


async def test_healed_views_alone_are_yellow(client, workspace_ctx):
    tour = await _live_tour(client, workspace_ctx)
    await insert_events(
        workspace_ctx.id,
        tour["id"],
        [(None, "started", None), (None, "step_viewed", 0, {"healed": True})],
    )
    health = await _health(workspace_ctx.id, tour["id"])
    assert health["health"] == "yellow"
    assert health["healed_step_views"] == 1
