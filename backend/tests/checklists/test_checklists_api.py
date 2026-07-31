"""App checklists API: CRUD, item validation matrix, publish gating, stats,
authz, and workspace isolation."""

from __future__ import annotations

from app.core.security import create_widget_token
from tests.checklists.conftest import (
    THREE_ITEMS,
    create_checklist,
    create_contact,
    publish_checklist,
    widget_key_for,
)


async def test_checklist_crud_lifecycle(client, workspace_ctx):
    checklist = await create_checklist(client, workspace_ctx, name="Onboarding")
    assert checklist["status"] == "draft"
    assert checklist["version"] == 1
    assert len(checklist["items"]) == 3
    assert all(item["id"] for item in checklist["items"])  # ids assigned
    assert checklist["launcher"] == {"label": "Getting started", "auto_open_once": True}
    assert checklist["theme"]["position"] == "bottom-right"

    listing = await client.get(
        f"{workspace_ctx.base}/checklists", headers=workspace_ctx.owner_headers
    )
    assert [c["name"] for c in listing.json()] == ["Onboarding"]

    got = await client.get(
        f"{workspace_ctx.base}/checklists/{checklist['id']}", headers=workspace_ctx.owner_headers
    )
    assert got.json()["name"] == "Onboarding"

    published = await publish_checklist(client, workspace_ctx, checklist["id"])
    assert published["status"] == "live"

    paused = await client.post(
        f"{workspace_ctx.base}/checklists/{checklist['id']}/pause",
        headers=workspace_ctx.owner_headers,
    )
    assert paused.json()["status"] == "paused"

    deleted = await client.delete(
        f"{workspace_ctx.base}/checklists/{checklist['id']}", headers=workspace_ctx.owner_headers
    )
    assert deleted.status_code == 200
    gone = await client.get(
        f"{workspace_ctx.base}/checklists/{checklist['id']}", headers=workspace_ctx.owner_headers
    )
    assert gone.status_code == 404


async def test_version_bumps_only_on_item_change(client, workspace_ctx):
    checklist = await create_checklist(client, workspace_ctx)
    cid = checklist["id"]

    renamed = await client.patch(
        f"{workspace_ctx.base}/checklists/{cid}",
        json={"name": "Renamed"},
        headers=workspace_ctx.owner_headers,
    )
    assert renamed.json()["version"] == 1

    same = await client.patch(
        f"{workspace_ctx.base}/checklists/{cid}",
        json={"items": THREE_ITEMS},
        headers=workspace_ctx.owner_headers,
    )
    assert same.json()["version"] == 1

    changed = await client.patch(
        f"{workspace_ctx.base}/checklists/{cid}",
        json={"items": [{"title": "Only one thing"}]},
        headers=workspace_ctx.owner_headers,
    )
    assert changed.json()["version"] == 2
    assert len(changed.json()["items"]) == 1


async def test_item_action_and_completion_round_trip(client, workspace_ctx):
    checklist = await create_checklist(
        client,
        workspace_ctx,
        items=[
            {
                "title": "Take the tour",
                "action": {"type": "start_tour", "tour_id": "tour-1"},
                "completion": {"type": "tour_completed", "tour_id": "tour-1"},
            },
            {
                "title": "Read the docs",
                "action": {"type": "open_url", "url": "https://docs.example.com"},
                "completion": {"type": "url_visited", "url_pattern": "*/docs*"},
            },
            {"title": "Say hi", "action": {"type": "open_messenger"}},
        ],
        trigger={"type": "url_match", "url_pattern": "*/app*"},
        audience={
            "type": "filters",
            "filters": [{"field": "attributes.plan", "op": "eq", "value": "pro"}],
        },
        theme={"accent": "#ff0000", "position": "bottom-left"},
        launcher={"label": "Set up", "auto_open_once": False},
        priority=7,
    )
    items = checklist["items"]
    assert items[0]["action"] == {"type": "start_tour", "tour_id": "tour-1", "url": None}
    assert items[0]["completion"]["type"] == "tour_completed"
    assert items[1]["completion"]["url_pattern"] == "*/docs*"
    assert items[2]["action"]["type"] == "open_messenger"
    assert checklist["trigger"] == {"type": "url_match", "url_pattern": "*/app*"}
    assert checklist["audience"]["filters"][0]["field"] == "attributes.plan"
    assert checklist["theme"] == {"accent": "#ff0000", "position": "bottom-left"}
    assert checklist["launcher"] == {"label": "Set up", "auto_open_once": False}
    assert checklist["priority"] == 7


async def test_item_validation_matrix(client, workspace_ctx):
    cases = [
        # start_tour without a tour id
        [{"title": "x", "action": {"type": "start_tour"}}],
        # open_url without a url
        [{"title": "x", "action": {"type": "open_url"}}],
        # tour_completed without a tour id
        [{"title": "x", "completion": {"type": "tour_completed"}}],
        # url_visited without a pattern
        [{"title": "x", "completion": {"type": "url_visited"}}],
        # blank title
        [{"title": ""}],
        # unknown action type
        [{"title": "x", "action": {"type": "explode"}}],
        # too many items
        [{"title": f"Item {i}"} for i in range(21)],
    ]
    for items in cases:
        resp = await client.post(
            f"{workspace_ctx.base}/checklists",
            json={"name": "Bad", "items": items},
            headers=workspace_ctx.owner_headers,
        )
        assert resp.status_code == 422, (items, resp.text)


async def test_publish_requires_items(client, workspace_ctx):
    empty = await create_checklist(client, workspace_ctx, name="Empty", items=[])
    resp = await client.post(
        f"{workspace_ctx.base}/checklists/{empty['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    assert resp.status_code == 409


async def test_stats_reflect_progress(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    checklist = await create_checklist(client, workspace_ctx)
    await publish_checklist(client, workspace_ctx, checklist["id"])
    item_ids = [i["id"] for i in checklist["items"]]

    finisher = await create_contact(client, workspace_ctx, name="Finisher")
    starter = await create_contact(client, workspace_ctx, name="Starter")
    for contact, done_items in ((finisher, item_ids), (starter, item_ids[:1])):
        headers = {"X-Widget-Token": create_widget_token(workspace_ctx.id, contact["id"])}
        for item_id in done_items:
            resp = await client.post(
                f"/api/widget/checklists/{checklist['id']}/progress?widget_key={key}",
                json={"item_id": item_id, "done": True},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text

    stats = await client.get(
        f"{workspace_ctx.base}/checklists/{checklist['id']}/stats",
        headers=workspace_ctx.owner_headers,
    )
    body = stats.json()
    assert body["starts"] == 2
    assert body["completions"] == 1
    assert body["completion_rate"] == 0.5
    assert body["views"] is None
    assert [i["completed_count"] for i in body["items"]] == [2, 1, 1]


async def test_authz_viewer_cannot_mutate(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("viewer@example.com", role="viewer")
    checklist = await create_checklist(client, workspace_ctx)

    readable = await client.get(f"{workspace_ctx.base}/checklists", headers=viewer)
    assert readable.status_code == 200

    created = await client.post(
        f"{workspace_ctx.base}/checklists", json={"name": "Nope"}, headers=viewer
    )
    assert created.status_code == 403

    patched = await client.patch(
        f"{workspace_ctx.base}/checklists/{checklist['id']}", json={"name": "Nope"}, headers=viewer
    )
    assert patched.status_code == 403

    published = await client.post(
        f"{workspace_ctx.base}/checklists/{checklist['id']}/publish", headers=viewer
    )
    assert published.status_code == 403

    deleted = await client.delete(
        f"{workspace_ctx.base}/checklists/{checklist['id']}", headers=viewer
    )
    assert deleted.status_code == 403


async def test_cross_workspace_isolation(client, workspace_ctx):
    checklist = await create_checklist(client, workspace_ctx)
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=workspace_ctx.owner_headers
    )
    other_base = f"/api/v1/w/{other.json()['id']}"

    seen = await client.get(f"{other_base}/checklists", headers=workspace_ctx.owner_headers)
    assert seen.json() == []

    foreign = await client.get(
        f"{other_base}/checklists/{checklist['id']}", headers=workspace_ctx.owner_headers
    )
    assert foreign.status_code == 404

    stats = await client.get(
        f"{other_base}/checklists/{checklist['id']}/stats", headers=workspace_ctx.owner_headers
    )
    assert stats.status_code == 404
