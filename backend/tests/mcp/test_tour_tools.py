"""list_tours / get_tour_steps / tours_health."""

from __future__ import annotations

from tests.mcp.conftest import call_tool, make_api_key

STEPS = [
    {"selector": '[data-tour="inbox"]', "title": "Inbox", "body": "Where threads live"},
    {"selector": '[data-tour="ai"]', "title": "AI", "body": "Meet the copilot"},
]


async def create_tour(client, ctx, *, name="Onboarding", steps=STEPS, publish=False) -> str:
    response = await client.post(
        f"{ctx.base}/tours", json={"name": name, "steps": steps}, headers=ctx.owner_headers
    )
    assert response.status_code == 201, response.text
    tour_id = response.json()["id"]
    if publish:
        published = await client.post(
            f"{ctx.base}/tours/{tour_id}/publish", headers=ctx.owner_headers
        )
        assert published.status_code == 200, published.text
    return tour_id


async def insert_events(workspace_id: str, tour_id: str, plays: list[tuple]) -> None:
    from app.core.db import session_scope
    from app.models.tour import TourEvent

    async with session_scope() as session:
        for contact_id, event, step_index, meta in plays:
            session.add(
                TourEvent(
                    workspace_id=workspace_id,
                    tour_id=tour_id,
                    contact_id=contact_id,
                    event=event,
                    step_index=step_index,
                    meta=meta,
                )
            )


async def test_list_tours_and_get_steps(client, workspace_ctx):
    live_id = await create_tour(client, workspace_ctx, name="Live tour", publish=True)
    await create_tour(client, workspace_ctx, name="Draft tour")
    key = (await make_api_key(client, workspace_ctx))["key"]

    tours = await call_tool(client, "list_tours", {}, key=key)
    assert {t["name"] for t in tours} == {"Live tour", "Draft tour"}
    live = next(t for t in tours if t["id"] == live_id)
    assert live["status"] == "live"
    assert live["kind"] == "flow"
    assert live["steps_count"] == 2

    only_live = await call_tool(client, "list_tours", {"status": "live"}, key=key)
    assert [t["id"] for t in only_live] == [live_id]

    steps = await call_tool(client, "get_tour_steps", {"tour_id": live_id}, key=key)
    assert steps["tour_id"] == live_id
    assert steps["total_steps"] == 2
    assert steps["steps"][0] == {
        "n": 1,
        "kind": "tooltip",
        "title": "Inbox",
        "body": "Where threads live",
        "selector": '[data-tour="inbox"]',
    }

    missing = await call_tool(client, "get_tour_steps", {"tour_id": "nope"}, key=key)
    assert missing == {"error": "Tour not found"}


async def test_tours_health_single_tour_red_with_broken_steps(client, workspace_ctx):
    tour_id = await create_tour(client, workspace_ctx, publish=True)
    await insert_events(
        workspace_ctx.id,
        tour_id,
        [
            ("c1", "started", None, {}),
            ("c1", "step_viewed", 0, {"healed": True}),
            ("c1", "step_error", 1, {"reason": "not_found"}),
            ("c2", "started", None, {}),
            ("c2", "step_error", 1, {"reason": "not_found"}),
        ],
    )
    key = (await make_api_key(client, workspace_ctx))["key"]
    health = await call_tool(client, "tours_health", {"tour_id": tour_id}, key=key)
    assert health["health"] == "red"
    assert health["broken_steps"] == [{"index": 1, "title": "AI", "errors": 2, "healed": 0}]
    assert health["healed_step_views"] == 1
    assert health["last_played_at"] is not None


async def test_tours_health_workspace_aggregate(client, workspace_ctx):
    healed_id = await create_tour(client, workspace_ctx, name="Drifting", publish=True)
    clean_id = await create_tour(client, workspace_ctx, name="Clean", publish=True)
    await insert_events(
        workspace_ctx.id,
        healed_id,
        [("c1", "started", None, {}), ("c1", "step_viewed", 0, {"healed": True})],
    )
    key = (await make_api_key(client, workspace_ctx))["key"]

    aggregate = await call_tool(client, "tours_health", {}, key=key)
    assert aggregate["workspace_health"] == "yellow"
    assert aggregate["totals"] == {"tours": 2, "green": 1, "yellow": 1, "red": 0}
    by_id = {t["tour_id"]: t for t in aggregate["tours"]}
    assert by_id[healed_id]["health"] == "yellow"
    assert by_id[healed_id]["broken_steps"] == []
    assert by_id[clean_id]["health"] == "green"
    assert by_id[clean_id]["last_played_at"] is None
