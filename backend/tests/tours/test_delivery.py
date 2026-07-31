"""Delivery v2: schedule windows, frequency matrix, priority ordering, cap,
manual start by id, preview tokens, and the disabled-inbox fix."""

from __future__ import annotations

from datetime import timedelta

from app.core.db import utcnow
from app.core.security import create_widget_token
from tests.tours.conftest import (
    create_contact,
    create_tour,
    insert_events,
    preview_token_for,
    publish_tour,
    widget_inbox,
    widget_key_for,
)

INBOX_URL = "https://app.example.com/inbox?tab=open"


async def _live(client, ctx, **overrides):
    payload = {
        "name": overrides.pop("name", "Live"),
        "trigger": {"type": "url_match", "url_pattern": "*/inbox*"},
    }
    payload.update(overrides)
    tour = await create_tour(client, ctx, **payload)
    await publish_tour(client, ctx, tour["id"])
    return tour


async def _delivered(client, key, headers=None) -> list[str]:
    resp = await client.get(
        f"/api/widget/tours?widget_key={key}&url={INBOX_URL}", headers=headers or {}
    )
    assert resp.status_code == 200, resp.text
    return [t["id"] for t in resp.json()]


async def _identified(client, ctx, name="Ada"):
    contact = await create_contact(client, ctx, name=name)
    return contact, {"X-Widget-Token": create_widget_token(ctx.id, contact["id"])}


# --- schedule ---------------------------------------------------------------


async def test_schedule_window_gates_delivery(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    now = utcnow()
    future = await _live(
        client,
        workspace_ctx,
        name="Future",
        schedule={"start_at": (now + timedelta(days=1)).isoformat()},
    )
    past = await _live(
        client,
        workspace_ctx,
        name="Expired",
        schedule={"end_at": (now - timedelta(days=1)).isoformat()},
    )
    current = await _live(
        client,
        workspace_ctx,
        name="Current",
        schedule={
            "start_at": (now - timedelta(days=1)).isoformat(),
            "end_at": (now + timedelta(days=1)).isoformat(),
        },
    )
    delivered = await _delivered(client, key)
    assert current["id"] in delivered
    assert future["id"] not in delivered
    assert past["id"] not in delivered


# --- frequency --------------------------------------------------------------


async def test_frequency_once_excludes_after_start(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    contact, headers = await _identified(client, workspace_ctx)
    tour = await _live(client, workspace_ctx, frequency={"type": "once"})

    assert await _delivered(client, key, headers) == [tour["id"]]
    await insert_events(workspace_ctx.id, tour["id"], [(contact["id"], "started", None)])
    assert await _delivered(client, key, headers) == []
    # Anonymous visitors are still served (the widget's seen-set guards them).
    assert await _delivered(client, key) == [tour["id"]]


async def test_frequency_until_completed_ignores_dismissals(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    contact, headers = await _identified(client, workspace_ctx)
    tour = await _live(client, workspace_ctx, frequency={"type": "until_completed"})

    await insert_events(workspace_ctx.id, tour["id"], [(contact["id"], "dismissed", None)])
    assert await _delivered(client, key, headers) == [tour["id"]], "dismissal must not exclude"

    await insert_events(workspace_ctx.id, tour["id"], [(contact["id"], "completed", None)])
    assert await _delivered(client, key, headers) == []


async def test_frequency_until_dismissed_is_the_default(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    contact, headers = await _identified(client, workspace_ctx)
    tour = await _live(client, workspace_ctx)  # no explicit frequency
    assert await _delivered(client, key, headers) == [tour["id"]]
    await insert_events(workspace_ctx.id, tour["id"], [(contact["id"], "dismissed", None)])
    assert await _delivered(client, key, headers) == []


async def test_frequency_every_time_always_delivers(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    contact, headers = await _identified(client, workspace_ctx)
    tour = await _live(client, workspace_ctx, frequency={"type": "every_time"})
    await insert_events(
        workspace_ctx.id,
        tour["id"],
        [(contact["id"], "started", None), (contact["id"], "completed", None)],
    )
    assert await _delivered(client, key, headers) == [tour["id"]]


async def test_every_time_respects_cooldown(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    contact, headers = await _identified(client, workspace_ctx)
    tour = await _live(client, workspace_ctx, frequency={"type": "every_time", "cooldown_hours": 6})
    now = utcnow()

    await insert_events(
        workspace_ctx.id,
        tour["id"],
        [(contact["id"], "completed", None, {}, now - timedelta(hours=1))],
    )
    assert await _delivered(client, key, headers) == [], "inside the cooldown"

    await insert_events(
        workspace_ctx.id,
        tour["id"],
        [(contact["id"], "started", None, {}, now - timedelta(hours=9))],
    )
    # Latest event still 1h old → still cooling down.
    assert await _delivered(client, key, headers) == []


async def test_cooldown_elapsed_redelivers(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    contact, headers = await _identified(client, workspace_ctx)
    tour = await _live(client, workspace_ctx, frequency={"type": "every_time", "cooldown_hours": 2})
    await insert_events(
        workspace_ctx.id,
        tour["id"],
        [(contact["id"], "completed", None, {}, utcnow() - timedelta(hours=5))],
    )
    assert await _delivered(client, key, headers) == [tour["id"]]


async def test_anonymous_visitor_never_excluded_by_history(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    contact, _headers = await _identified(client, workspace_ctx)
    tour = await _live(client, workspace_ctx, frequency={"type": "once"})
    await insert_events(workspace_ctx.id, tour["id"], [(contact["id"], "completed", None)])
    assert await _delivered(client, key) == [tour["id"]]


# --- ordering + cap ---------------------------------------------------------


async def test_priority_orders_delivery(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    low = await _live(client, workspace_ctx, name="Low", priority=0)
    high = await _live(client, workspace_ctx, name="High", priority=50)
    mid = await _live(client, workspace_ctx, name="Mid", priority=10)
    assert await _delivered(client, key) == [high["id"], mid["id"], low["id"]]


async def test_delivery_capped_at_five(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    for i in range(7):
        await _live(client, workspace_ctx, name=f"Tour {i}")
    assert len(await _delivered(client, key)) == 5


# --- manual start + preview -------------------------------------------------


async def test_manual_tour_fetchable_by_id(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    manual = await create_tour(client, workspace_ctx, name="Manual", trigger={"type": "manual"})
    await publish_tour(client, workspace_ctx, manual["id"])

    # Not auto-delivered …
    assert await _delivered(client, key) == []
    # … but startTour(id) resolves it.
    resp = await client.get(f"/api/widget/tours/{manual['id']}?widget_key={key}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == manual["id"]
    assert set(body) == {
        "id",
        "name",
        "kind",
        "steps",
        "theme",
        "version",
        "settings",
        "frequency_type",
    }


async def test_manual_fetch_404s_for_draft_and_frequency_exclusion(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    draft = await create_tour(client, workspace_ctx, name="Draft")
    assert (
        await client.get(f"/api/widget/tours/{draft['id']}?widget_key={key}")
    ).status_code == 404

    contact, headers = await _identified(client, workspace_ctx)
    seen = await _live(client, workspace_ctx, name="Seen", frequency={"type": "once"})
    await insert_events(workspace_ctx.id, seen["id"], [(contact["id"], "started", None)])
    excluded = await client.get(f"/api/widget/tours/{seen['id']}?widget_key={key}", headers=headers)
    assert excluded.status_code == 404


async def test_manual_fetch_requires_a_key(client, workspace_ctx):
    tour = await _live(client, workspace_ctx)
    assert (await client.get(f"/api/widget/tours/{tour['id']}")).status_code == 404
    assert (
        await client.get(f"/api/widget/tours/{tour['id']}?widget_key=wk_nope")
    ).status_code == 404


async def test_preview_token_returns_draft_regardless_of_status(client, workspace_ctx):
    draft = await create_tour(client, workspace_ctx, name="Preview me")
    token = await preview_token_for(client, workspace_ctx, draft["id"])
    resp = await client.get(f"/api/widget/tours/{draft['id']}?preview_token={token}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == draft["id"]


async def test_preview_token_is_scoped_to_one_tour(client, workspace_ctx):
    a = await create_tour(client, workspace_ctx, name="A")
    b = await create_tour(client, workspace_ctx, name="B")
    token = await preview_token_for(client, workspace_ctx, a["id"])
    wrong = await client.get(f"/api/widget/tours/{b['id']}?preview_token={token}")
    assert wrong.status_code == 404


async def test_preview_token_bad_or_expired_401(client, workspace_ctx):
    import jwt

    from app.core.config import get_settings

    tour = await create_tour(client, workspace_ctx, name="Expiring")
    garbage = await client.get(f"/api/widget/tours/{tour['id']}?preview_token=nope")
    assert garbage.status_code == 401

    now = utcnow()
    expired = jwt.encode(
        {
            "ws": workspace_ctx.id,
            "tour": tour["id"],
            "iss": "stept",
            "typ": "tour_preview",
            "iat": int((now - timedelta(hours=3)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        get_settings().secret_key,
        algorithm="HS256",
    )
    resp = await client.get(f"/api/widget/tours/{tour['id']}?preview_token={expired}")
    assert resp.status_code == 401


async def test_preview_token_requires_manage(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx)
    viewer = await workspace_ctx.add_member("preview-viewer@example.com", role="viewer")
    denied = await client.post(
        f"{workspace_ctx.base}/tours/{tour['id']}/preview-token", headers=viewer
    )
    assert denied.status_code == 403


# --- inbox resolution -------------------------------------------------------


async def test_disabled_widget_inbox_is_404(client, workspace_ctx):
    inbox = await widget_inbox(client, workspace_ctx)
    key = inbox["widget_key"]
    await _live(client, workspace_ctx)
    assert (await _delivered(client, key)) != []

    disabled = await client.patch(
        f"{workspace_ctx.base}/inboxes/{inbox['id']}",
        json={"enabled": False},
        headers=workspace_ctx.owner_headers,
    )
    assert disabled.status_code == 200, disabled.text

    resp = await client.get(f"/api/widget/tours?widget_key={key}&url={INBOX_URL}")
    assert resp.status_code == 404
    events = await client.post(
        f"/api/widget/tours/{(await create_tour(client, workspace_ctx))['id']}"
        f"/events?widget_key={key}",
        json={"event": "started"},
    )
    assert events.status_code == 404
