"""Widget public delivery: URL/audience targeting, exclusion, event recording."""

from __future__ import annotations

from app.core.security import create_widget_token
from tests.tours.conftest import (
    TWO_STEPS,
    create_contact,
    create_tour,
    publish_tour,
    widget_key_for,
)

INBOX_URL = "https://app.example.com/inbox?tab=open"


async def _live_url_tour(client, ctx, *, audience=None, url_pattern="*/inbox*", name="Live"):
    tour = await create_tour(
        client,
        ctx,
        name=name,
        trigger={"type": "url_match", "url_pattern": url_pattern},
        audience=audience or {"type": "all"},
    )
    await publish_tour(client, ctx, tour["id"])
    return tour


async def test_widget_delivers_matching_live_tour(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    tour = await _live_url_tour(client, workspace_ctx)

    match = await client.get(f"/api/widget/tours?widget_key={key}&url={INBOX_URL}")
    assert match.status_code == 200, match.text
    body = match.json()
    assert [t["id"] for t in body] == [tour["id"]]
    # Public shape: playback fields only — never audience/schedule internals.
    assert set(body[0]) == {
        "id",
        "name",
        "kind",
        "steps",
        "theme",
        "version",
        "settings",
        "frequency_type",
    }
    assert len(body[0]["steps"]) == len(TWO_STEPS)

    no_match = await client.get(
        f"/api/widget/tours?widget_key={key}&url=https://app.example.com/settings"
    )
    assert no_match.json() == []


async def test_widget_excludes_manual_draft_and_paused(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)

    # Manual trigger — never auto-delivered even when live.
    manual = await create_tour(client, workspace_ctx, name="Manual", trigger={"type": "manual"})
    await publish_tour(client, workspace_ctx, manual["id"])

    # URL match but still a draft.
    await create_tour(
        client,
        workspace_ctx,
        name="Draft",
        trigger={"type": "url_match", "url_pattern": "*/inbox*"},
    )

    # URL match, published then paused.
    paused = await _live_url_tour(client, workspace_ctx, name="Paused")
    await client.post(
        f"{workspace_ctx.base}/tours/{paused['id']}/pause", headers=workspace_ctx.owner_headers
    )

    resp = await client.get(f"/api/widget/tours?widget_key={key}&url={INBOX_URL}")
    assert resp.json() == []


async def test_widget_audience_filters_target_contact(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    ent = await create_contact(client, workspace_ctx, name="Ent", attributes={"plan": "enterprise"})
    free = await create_contact(client, workspace_ctx, name="Free", attributes={"plan": "free"})
    tour = await _live_url_tour(
        client,
        workspace_ctx,
        audience={
            "type": "filters",
            "filters": [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
        },
    )

    ent_token = create_widget_token(workspace_ctx.id, ent["id"])
    free_token = create_widget_token(workspace_ctx.id, free["id"])

    ent_resp = await client.get(
        f"/api/widget/tours?widget_key={key}&url={INBOX_URL}",
        headers={"X-Widget-Token": ent_token},
    )
    assert [t["id"] for t in ent_resp.json()] == [tour["id"]]

    free_resp = await client.get(
        f"/api/widget/tours?widget_key={key}&url={INBOX_URL}",
        headers={"X-Widget-Token": free_token},
    )
    assert free_resp.json() == []

    # Anonymous visitors never match a filters audience.
    anon = await client.get(f"/api/widget/tours?widget_key={key}&url={INBOX_URL}")
    assert anon.json() == []


async def test_widget_all_audience_reaches_anonymous(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    tour = await _live_url_tour(client, workspace_ctx, audience={"type": "all"})
    resp = await client.get(f"/api/widget/tours?widget_key={key}&url={INBOX_URL}")
    assert [t["id"] for t in resp.json()] == [tour["id"]]


async def test_widget_excludes_completed_or_dismissed(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    contact = await create_contact(client, workspace_ctx, name="Repeat")
    token = create_widget_token(workspace_ctx.id, contact["id"])
    headers = {"X-Widget-Token": token}
    tour = await _live_url_tour(client, workspace_ctx)

    first = await client.get(f"/api/widget/tours?widget_key={key}&url={INBOX_URL}", headers=headers)
    assert [t["id"] for t in first.json()] == [tour["id"]]

    done = await client.post(
        f"/api/widget/tours/{tour['id']}/events?widget_key={key}",
        json={"event": "completed"},
        headers=headers,
    )
    assert done.status_code == 200

    again = await client.get(f"/api/widget/tours?widget_key={key}&url={INBOX_URL}", headers=headers)
    assert again.json() == []
    # A different (anonymous) visitor still sees it.
    other = await client.get(f"/api/widget/tours?widget_key={key}&url={INBOX_URL}")
    assert [t["id"] for t in other.json()] == [tour["id"]]


async def test_widget_event_recording_and_validation(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    tour = await _live_url_tour(client, workspace_ctx)

    ok = await client.post(
        f"/api/widget/tours/{tour['id']}/events?widget_key={key}",
        json={"event": "step_viewed", "step_index": 1},
    )
    assert ok.status_code == 200
    assert ok.json()["message"] == "recorded"

    # Tolerant ingestion (lifecycle contract): an unknown event type from a
    # newer/odd widget build is accepted and IGNORED — never a 422, never a row.
    unknown_event = await client.post(
        f"/api/widget/tours/{tour['id']}/events?widget_key={key}",
        json={"event": "clicked"},
    )
    assert unknown_event.status_code == 200
    assert unknown_event.json()["message"] == "ignored"

    # `step_blocked` (visitor pressed Next, next anchor missing) is a first-
    # class lifecycle event and records like any other.
    blocked = await client.post(
        f"/api/widget/tours/{tour['id']}/events?widget_key={key}",
        json={"event": "step_blocked", "step_index": 1},
    )
    assert blocked.status_code == 200
    assert blocked.json()["message"] == "recorded"

    missing_key = await client.post(
        f"/api/widget/tours/{tour['id']}/events",
        json={"event": "started"},
    )
    assert missing_key.status_code == 422

    unknown_tour = await client.post(
        f"/api/widget/tours/00000000-0000-7000-8000-0000000000ff/events?widget_key={key}",
        json={"event": "started"},
    )
    assert unknown_tour.status_code == 404


async def test_widget_unknown_key_404(client, workspace_ctx):
    resp = await client.get(f"/api/widget/tours?widget_key=wk_nope&url={INBOX_URL}")
    assert resp.status_code == 404


async def test_widget_event_cross_workspace_404(client, workspace_ctx):
    # A tour that lives in a second workspace is unreachable via this key.
    key = await widget_key_for(client, workspace_ctx)
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=workspace_ctx.owner_headers
    )
    other_base = f"/api/v1/w/{other.json()['id']}"
    foreign = await client.post(
        f"{other_base}/tours",
        json={"name": "Foreign", "steps": TWO_STEPS},
        headers=workspace_ctx.owner_headers,
    )
    resp = await client.post(
        f"/api/widget/tours/{foreign.json()['id']}/events?widget_key={key}",
        json={"event": "started"},
    )
    assert resp.status_code == 404
