"""Analytics v2: unique starts, healed steps, step errors, by-day series,
event feed pagination, meta persistence and realtime broadcast."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app.core.db import utcnow
from app.core.security import create_widget_token
from tests.tours.conftest import (
    THREE_STEPS,
    create_contact,
    create_tour,
    insert_events,
    publish_tour,
    widget_key_for,
)


async def _stats(client, ctx, tour_id) -> dict:
    resp = await client.get(f"{ctx.base}/tours/{tour_id}/stats", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_stats_v2_unique_healed_and_errors(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx, steps=THREE_STEPS)
    tid = tour["id"]
    await insert_events(
        workspace_ctx.id,
        tid,
        [
            # c1 starts twice (one visitor, two sessions) and completes
            ("c1", "started", None),
            ("c1", "started", None),
            ("c1", "step_viewed", 0, {"healed": True}),
            ("c1", "step_viewed", 1),
            ("c1", "step_viewed", 2),
            ("c1", "completed", None),
            # c2 hits a broken selector
            ("c2", "started", None),
            ("c2", "step_viewed", 0, {"healed": True}),
            ("c2", "step_error", 1, {"reason": "not_found"}),
            ("c2", "dismissed", None),
            # two anonymous starts count individually
            (None, "started", None),
            (None, "started", None),
        ],
    )
    body = await _stats(client, workspace_ctx, tid)
    assert body["starts"] == 5
    assert body["unique_starts"] == 4  # c1 + c2 + 2 anonymous
    assert body["completions"] == 1
    assert body["dismissals"] == 1
    assert body["step_errors"] == 1
    assert [s["viewed"] for s in body["steps"]] == [2, 1, 1]
    assert [s["healed"] for s in body["steps"]] == [2, 0, 0]
    assert [s["drop_off"] for s in body["steps"]] == [1, 0, 0]


async def test_stats_by_day_series(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx, steps=THREE_STEPS)
    tid = tour["id"]
    now = utcnow()
    await insert_events(
        workspace_ctx.id,
        tid,
        [
            ("c1", "started", None, {}, now),
            ("c2", "started", None, {}, now - timedelta(days=2)),
            ("c2", "completed", None, {}, now - timedelta(days=2)),
            # Outside the 30-day window: counted in totals, not in the series.
            ("c3", "started", None, {}, now - timedelta(days=45)),
        ],
    )
    body = await _stats(client, workspace_ctx, tid)
    assert body["starts"] == 3
    series = body["by_day"]
    assert len(series) == 30
    assert series[-1]["date"] == now.date().isoformat()
    assert series[-1]["starts"] == 1
    by_date = {d["date"]: d for d in series}
    two_days_ago = (now - timedelta(days=2)).date().isoformat()
    assert by_date[two_days_ago] == {"date": two_days_ago, "starts": 1, "completions": 1}
    assert sum(d["starts"] for d in series) == 2  # the 45-day-old start is excluded


async def test_stats_zeroed_for_untouched_tour(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx)
    body = await _stats(client, workspace_ctx, tour["id"])
    assert body["starts"] == 0
    assert body["unique_starts"] == 0
    assert body["step_errors"] == 0
    assert body["completion_rate"] == 0.0
    assert all(d["starts"] == 0 for d in body["by_day"])


async def test_events_feed_paginates_newest_first(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx, steps=THREE_STEPS)
    tid = tour["id"]
    now = utcnow()
    await insert_events(
        workspace_ctx.id,
        tid,
        [("c1", "step_viewed", i, {"n": i}, now + timedelta(seconds=i)) for i in range(5)],
    )
    first = await client.get(
        f"{workspace_ctx.base}/tours/{tid}/events?limit=2", headers=workspace_ctx.owner_headers
    )
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["total"] == 5
    assert page["limit"] == 2
    assert [i["step_index"] for i in page["items"]] == [4, 3]
    assert page["items"][0]["meta"] == {"n": 4}
    assert page["items"][0]["contact_id"] == "c1"

    second = await client.get(
        f"{workspace_ctx.base}/tours/{tid}/events?limit=2&offset=2",
        headers=workspace_ctx.owner_headers,
    )
    assert [i["step_index"] for i in second.json()["items"]] == [2, 1]


async def test_events_feed_authz_and_isolation(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx)
    viewer = await workspace_ctx.add_member("events-viewer@example.com", role="viewer")
    # tours:read is enough to read the feed.
    ok = await client.get(f"{workspace_ctx.base}/tours/{tour['id']}/events", headers=viewer)
    assert ok.status_code == 200

    from tests.conftest import bearer, signup

    outsider = bearer(await signup(client, "events-outsider@example.com"))
    assert (
        await client.get(f"{workspace_ctx.base}/tours/{tour['id']}/events", headers=outsider)
    ).status_code == 403

    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other"}, headers=workspace_ctx.owner_headers
    )
    leaked = await client.get(
        f"/api/v1/w/{other.json()['id']}/tours/{tour['id']}/events",
        headers=workspace_ctx.owner_headers,
    )
    assert leaked.status_code == 404


async def test_widget_event_persists_meta_and_step_error(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    tour = await create_tour(client, workspace_ctx, steps=THREE_STEPS)
    await publish_tour(client, workspace_ctx, tour["id"])
    contact = await create_contact(client, workspace_ctx, name="Meta")
    headers = {"X-Widget-Token": create_widget_token(workspace_ctx.id, contact["id"])}

    healed = await client.post(
        f"/api/widget/tours/{tour['id']}/events?widget_key={key}",
        json={
            "event": "step_viewed",
            "step_index": 0,
            "meta": {"url": "https://app.example.com/inbox", "viewport_w": 1440, "healed": True},
        },
        headers=headers,
    )
    assert healed.status_code == 200, healed.text

    errored = await client.post(
        f"/api/widget/tours/{tour['id']}/events?widget_key={key}",
        json={"event": "step_error", "step_index": 1, "meta": {"reason": "not_found"}},
        headers=headers,
    )
    assert errored.status_code == 200, errored.text

    feed = await client.get(
        f"{workspace_ctx.base}/tours/{tour['id']}/events", headers=workspace_ctx.owner_headers
    )
    items = feed.json()["items"]
    assert [i["event"] for i in items] == ["step_error", "step_viewed"]
    assert items[0]["meta"] == {"reason": "not_found"}
    assert items[1]["meta"]["healed"] is True
    assert items[1]["meta"]["viewport_w"] == 1440

    stats = await _stats(client, workspace_ctx, tour["id"])
    assert stats["step_errors"] == 1
    assert [s["healed"] for s in stats["steps"]] == [1, 0, 0]


async def test_oversized_event_meta_rejected(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    tour = await create_tour(client, workspace_ctx)
    await publish_tour(client, workspace_ctx, tour["id"])
    resp = await client.post(
        f"/api/widget/tours/{tour['id']}/events?widget_key={key}",
        json={"event": "started", "meta": {"blob": "z" * 5000}},
    )
    assert resp.status_code == 422


async def test_tour_event_is_broadcast_to_the_workspace_topic(client, workspace_ctx):
    """The analytics page live-appends from ws:{workspace_id}."""
    from app.core.pubsub import get_pubsub
    from app.realtime.manager import workspace_topic

    key = await widget_key_for(client, workspace_ctx)
    tour = await create_tour(client, workspace_ctx)
    await publish_tour(client, workspace_ctx, tour["id"])

    subscription = await get_pubsub().subscribe(workspace_topic(workspace_ctx.id))
    try:
        await client.post(
            f"/api/widget/tours/{tour['id']}/events?widget_key={key}",
            json={"event": "started", "meta": {"url": "https://app.example.com/inbox"}},
        )
        message = await asyncio.wait_for(subscription.queue.get(), 2.0)
    finally:
        await subscription.close()

    assert message["type"] == "tour.event"
    assert message["data"]["tour_id"] == tour["id"]
    assert message["data"]["event"] == "started"
    assert message["data"]["meta"]["url"] == "https://app.example.com/inbox"
