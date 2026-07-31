"""App tours API: CRUD, publish/pause, version bumping, stats, authz, isolation."""

from __future__ import annotations

from tests.tours.conftest import (
    THREE_STEPS,
    TWO_STEPS,
    create_tour,
    insert_events,
)


async def test_tour_crud_lifecycle(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx, name="Onboarding")
    assert tour["status"] == "draft"
    assert tour["version"] == 1
    assert len(tour["steps"]) == 2
    assert all(step["id"] for step in tour["steps"])  # ids assigned

    listing = await client.get(f"{workspace_ctx.base}/tours", headers=workspace_ctx.owner_headers)
    assert [t["name"] for t in listing.json()] == ["Onboarding"]

    got = await client.get(
        f"{workspace_ctx.base}/tours/{tour['id']}", headers=workspace_ctx.owner_headers
    )
    assert got.status_code == 200
    assert got.json()["name"] == "Onboarding"

    published = await client.post(
        f"{workspace_ctx.base}/tours/{tour['id']}/publish", headers=workspace_ctx.owner_headers
    )
    assert published.json()["status"] == "live"

    paused = await client.post(
        f"{workspace_ctx.base}/tours/{tour['id']}/pause", headers=workspace_ctx.owner_headers
    )
    assert paused.json()["status"] == "paused"

    deleted = await client.delete(
        f"{workspace_ctx.base}/tours/{tour['id']}", headers=workspace_ctx.owner_headers
    )
    assert deleted.status_code == 200
    gone = await client.get(
        f"{workspace_ctx.base}/tours/{tour['id']}", headers=workspace_ctx.owner_headers
    )
    assert gone.status_code == 404


async def test_create_round_trips_trigger_and_audience(client, workspace_ctx):
    tour = await create_tour(
        client,
        workspace_ctx,
        name="Targeted",
        trigger={"type": "url_match", "url_pattern": "*/inbox*"},
        audience={
            "type": "filters",
            "filters": [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
        },
        theme={"accent": "#ff0000"},
    )
    assert tour["trigger"] == {"type": "url_match", "url_pattern": "*/inbox*"}
    assert tour["audience"]["type"] == "filters"
    assert tour["audience"]["filters"][0]["field"] == "attributes.plan"
    assert tour["theme"]["accent"] == "#ff0000"


async def test_version_bumps_only_on_step_change(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx)
    tid = tour["id"]
    assert tour["version"] == 1

    # Renaming does not touch the version.
    renamed = await client.patch(
        f"{workspace_ctx.base}/tours/{tid}",
        json={"name": "Renamed"},
        headers=workspace_ctx.owner_headers,
    )
    assert renamed.json()["version"] == 1

    # Re-sending identical step content does not bump either.
    same = await client.patch(
        f"{workspace_ctx.base}/tours/{tid}",
        json={"steps": TWO_STEPS},
        headers=workspace_ctx.owner_headers,
    )
    assert same.json()["version"] == 1

    # Changed steps bump the version.
    changed = await client.patch(
        f"{workspace_ctx.base}/tours/{tid}",
        json={"steps": THREE_STEPS},
        headers=workspace_ctx.owner_headers,
    )
    assert changed.json()["version"] == 2
    assert len(changed.json()["steps"]) == 3


async def test_stats_funnel_math(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx, steps=THREE_STEPS)
    tid = tour["id"]
    await insert_events(
        workspace_ctx.id,
        tid,
        [
            ("c1", "started", None),
            ("c1", "step_viewed", 0),
            ("c1", "step_viewed", 1),
            ("c1", "step_viewed", 2),
            ("c1", "completed", None),
            ("c2", "started", None),
            ("c2", "step_viewed", 0),
            ("c2", "step_viewed", 1),
            ("c2", "dismissed", None),
            ("c3", "started", None),
            ("c3", "step_viewed", 0),
            ("c3", "dismissed", None),
        ],
    )
    stats = await client.get(
        f"{workspace_ctx.base}/tours/{tid}/stats", headers=workspace_ctx.owner_headers
    )
    assert stats.status_code == 200, stats.text
    body = stats.json()
    assert body["starts"] == 3
    assert body["completions"] == 1
    assert body["dismissals"] == 2
    assert body["completion_rate"] == 0.3333
    assert [s["viewed"] for s in body["steps"]] == [3, 2, 1]
    assert [s["drop_off"] for s in body["steps"]] == [1, 1, 0]
    assert [s["index"] for s in body["steps"]] == [0, 1, 2]


async def test_stats_empty_tour_is_zeroed(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx)
    stats = await client.get(
        f"{workspace_ctx.base}/tours/{tour['id']}/stats", headers=workspace_ctx.owner_headers
    )
    body = stats.json()
    assert body["starts"] == 0
    assert body["completion_rate"] == 0.0


async def test_manage_perms_enforced(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("tour-viewer@example.com", role="viewer")
    agent = await workspace_ctx.add_member("tour-agent2@example.com", role="agent")

    # tours:read lets viewers list.
    assert (await client.get(f"{workspace_ctx.base}/tours", headers=viewer)).status_code == 200

    # Neither viewer nor agent has tours:manage.
    for headers in (viewer, agent):
        blocked = await client.post(
            f"{workspace_ctx.base}/tours",
            json={"name": "Nope", "steps": TWO_STEPS},
            headers=headers,
        )
        assert blocked.status_code == 403

    # Owner has it.
    ok = await client.post(
        f"{workspace_ctx.base}/tours",
        json={"name": "Yes", "steps": TWO_STEPS},
        headers=workspace_ctx.owner_headers,
    )
    assert ok.status_code == 201


async def test_non_member_and_missing(client, workspace_ctx):
    from tests.conftest import bearer, signup

    outsider = bearer(await signup(client, "outsider-tour@example.com"))
    # No membership → forbidden.
    assert (await client.get(f"{workspace_ctx.base}/tours", headers=outsider)).status_code == 403
    # Missing tour → 404 for a legitimate member.
    missing = await client.get(
        f"{workspace_ctx.base}/tours/00000000-0000-7000-8000-0000000000ff",
        headers=workspace_ctx.owner_headers,
    )
    assert missing.status_code == 404


async def test_cross_workspace_isolation(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx, name="WS1 tour")
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Second WS"}, headers=workspace_ctx.owner_headers
    )
    other_base = f"/api/v1/w/{other.json()['id']}"

    # Same owner, different workspace scope → the tour is not visible there.
    leaked = await client.get(
        f"{other_base}/tours/{tour['id']}", headers=workspace_ctx.owner_headers
    )
    assert leaked.status_code == 404
    listing = await client.get(f"{other_base}/tours", headers=workspace_ctx.owner_headers)
    assert listing.json() == []
