"""Adoption analytics MCP tools.

These wrap existing stats services, so the tests focus on what this layer adds:
the funnel differenced into per-step drop-off with the worst step named, the
checklist friction point, and one cross-content ranking that includes draft and
paused content (because "nobody sees it" is usually the finding).
"""

from __future__ import annotations

from app.core.db import session_scope
from app.models.contact import Contact
from app.services import tours as tours_service
from tests.mcp.conftest import call_tool, list_tool_names, make_api_key


async def _key(client, ctx, scopes=("write",)) -> str:
    return (await make_api_key(client, ctx, scopes=list(scopes), name="analytics"))["key"]


async def _contact(workspace_id: str, email: str) -> str:
    async with session_scope() as session:
        contact = Contact(workspace_id=workspace_id, email=email)
        session.add(contact)
        await session.flush()
        return contact.id


async def _record(workspace_id, tour_id, event, contact_id, step_index=None, meta=None) -> None:
    async with session_scope() as session:
        await tours_service.record_event(
            session,
            workspace_id,
            tour_id,
            event=event,
            step_index=step_index,
            contact_id=contact_id,
            meta=meta,
        )


async def _three_step_tour(client, key) -> dict:
    tour = await call_tool(
        client,
        "create_tour",
        {
            "name": "Onboarding",
            "trigger": {"type": "url_match", "url_pattern": "*"},
            "steps": [
                {"type": "modal", "title": "Welcome", "body": "a"},
                {"type": "tooltip", "selector": "#two", "title": "Connect data", "body": "b"},
                {"type": "modal", "title": "Done", "body": "c"},
            ],
        },
        key=key,
    )
    await call_tool(client, "publish_tour", {"tour_id": tour["id"]}, key=key)
    return tour


async def test_analytics_tools_are_registered(client, workspace_ctx):
    assert {
        "get_tour_analytics",
        "get_checklist_analytics",
        "get_survey_results",
        "get_adoption_overview",
    } <= await list_tool_names(client)


async def test_tour_funnel_names_the_biggest_drop_off(client, workspace_ctx):
    """Three visitors start, all see step 0, two see step 1, none see step 2 —
    the readout must point at step 1 as where people are lost."""
    key = await _key(client, workspace_ctx)
    tour = await _three_step_tour(client, key)
    ws = workspace_ctx.id

    for index, email in enumerate(("a@x.test", "b@x.test", "c@x.test")):
        contact_id = await _contact(ws, email)
        await _record(ws, tour["id"], "started", contact_id)
        await _record(ws, tour["id"], "step_viewed", contact_id, step_index=0)
        if index < 2:
            await _record(ws, tour["id"], "step_viewed", contact_id, step_index=1)

    payload = await call_tool(client, "get_tour_analytics", {"tour_id": tour["id"]}, key=key)
    assert payload["starts"] == 3
    assert payload["completions"] == 0
    assert payload["completion_rate"] == 0.0

    biggest = payload["funnel"]["biggest_drop_off"]
    assert biggest is not None
    assert biggest["index"] == 1, payload["funnel"]["steps"]

    steps = payload["funnel"]["steps"]
    assert steps[0]["viewed"] == 3
    assert steps[0]["reach_rate"] == 1.0
    assert steps[1]["viewed"] == 2


async def test_tour_analytics_surfaces_playback_health(client, workspace_ctx):
    """A bad completion rate caused by a broken selector is a different fix from
    bad copy, so health rides along with the funnel."""
    key = await _key(client, workspace_ctx)
    tour = await _three_step_tour(client, key)
    ws = workspace_ctx.id
    contact_id = await _contact(ws, "err@x.test")
    await _record(ws, tour["id"], "started", contact_id)
    await _record(ws, tour["id"], "step_error", contact_id, step_index=1)

    payload = await call_tool(client, "get_tour_analytics", {"tour_id": tour["id"]}, key=key)
    assert payload["playback_health"]["health"] == "red"
    assert payload["playback_health"]["step_errors"] == 1
    assert payload["playback_health"]["broken_steps"][0]["index"] == 1


async def test_survey_results_readout(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    survey = await call_tool(
        client,
        "create_survey",
        {
            "name": "Post-onboarding",
            "questions": [{"type": "nps", "question": "How likely to recommend?"}],
        },
        key=key,
    )
    payload = await call_tool(client, "get_survey_results", {"survey_id": survey["id"]}, key=key)
    assert payload["id"] == survey["id"]
    assert payload["responses"] == 0
    assert payload["questions"][0]["type"] == "nps"


async def test_checklist_analytics_returns_per_item_rows(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    checklist = await call_tool(
        client,
        "create_checklist",
        {
            "name": "Setup",
            "items": [{"title": "Invite a teammate"}, {"title": "Connect a source"}],
        },
        key=key,
    )
    payload = await call_tool(
        client, "get_checklist_analytics", {"checklist_id": checklist["id"]}, key=key
    )
    assert [item["title"] for item in payload["items"]] == [
        "Invite a teammate",
        "Connect a source",
    ]
    assert payload["starts"] == 0
    assert payload["stalls_at"] is None


async def test_overview_ranks_by_reach_and_keeps_unpublished_rows(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    ws = workspace_ctx.id

    busy = await _three_step_tour(client, key)
    quiet = await call_tool(
        client,
        "create_tour",
        {"name": "Never seen", "steps": [{"type": "modal", "title": "Hi", "body": "x"}]},
        key=key,
    )
    for email in ("p@x.test", "q@x.test"):
        contact_id = await _contact(ws, email)
        await _record(ws, busy["id"], "started", contact_id)
        await _record(ws, busy["id"], "completed", contact_id)

    payload = await call_tool(client, "get_adoption_overview", {}, key=key)
    rows = {row["id"]: row for row in payload["content"]}

    assert payload["window_days"] == 30
    assert payload["content"][0]["id"] == busy["id"], "highest reach first"
    assert rows[busy["id"]]["reach"] == 2
    assert rows[busy["id"]]["completion_rate"] == 1.0
    assert rows[quiet["id"]]["reach"] == 0
    assert rows[quiet["id"]]["status"] == "draft", "draft content stays visible in the ranking"


async def test_overview_rejects_an_out_of_range_window(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    payload = await call_tool(client, "get_adoption_overview", {"days": 0}, key=key)
    assert "error" in payload
    payload = await call_tool(client, "get_adoption_overview", {"days": 5000}, key=key)
    assert "error" in payload


async def test_analytics_needs_reports_read_and_a_key(client, workspace_ctx):
    unauthenticated = await call_tool(client, "get_adoption_overview", {})
    assert unauthenticated == {"error": "Authentication required. Provide a valid API key."}

    # A read-scoped key carries reports:read — analysts should not need write.
    read_key = (await make_api_key(client, workspace_ctx, scopes=["read"], name="ro"))["key"]
    payload = await call_tool(client, "get_adoption_overview", {}, key=read_key)
    assert "error" not in payload
    assert payload["content"] == []


async def test_analytics_cannot_reach_another_workspace(client, workspace_ctx):
    from tests.conftest import bearer, signup

    key = await _key(client, workspace_ctx)
    tour = await _three_step_tour(client, key)

    other_auth = await signup(client, "outsider@example.com", name="Outsider")
    other_ws = await client.post(
        "/api/v1/workspaces", json={"name": "Other"}, headers=bearer(other_auth)
    )
    other_key = (
        await client.post(
            f"/api/v1/w/{other_ws.json()['id']}/api-keys",
            json={"name": "k", "scopes": ["read"]},
            headers=bearer(other_auth),
        )
    ).json()["key"]

    payload = await call_tool(client, "get_tour_analytics", {"tour_id": tour["id"]}, key=other_key)
    assert "not found" in payload["error"].lower()
