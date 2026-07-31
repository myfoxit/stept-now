"""Step schema v2: per-type validation matrix, passthrough fields, version bumps."""

from __future__ import annotations

import pytest

from tests.tours.conftest import create_tour

TOOLTIP = {"type": "tooltip", "selector": "#a", "title": "T"}


async def _post_steps(client, ctx, steps):
    return await client.post(
        f"{ctx.base}/tours",
        json={"name": "Schema", "steps": steps},
        headers=ctx.owner_headers,
    )


@pytest.mark.parametrize(
    "step",
    [
        {"type": "tooltip", "selector": ""},
        {"type": "hotspot", "selector": "   "},
        {"type": "action", "selector": "", "action": {"kind": "click"}},
        {"type": "action", "selector": "#a"},  # missing action config
        {"type": "wait", "selector": "#a"},  # missing wait config
        {"type": "wait", "selector": "#a", "wait": {"for": "url"}},  # url needs a pattern
        {"type": "wait", "wait": {"for": "element"}},  # element needs a selector
        {"type": "action", "selector": "#a", "action": {"kind": "fill"}},  # fill needs value
        {"type": "action", "selector": "#a", "action": {"kind": "navigate"}},  # needs url
        {"type": "tooltip", "selector": "#a", "advance": {"on": "delay"}},  # needs delay_ms
        {"type": "elephant", "selector": "#a"},  # unknown type
    ],
)
async def test_invalid_steps_rejected(client, workspace_ctx, step):
    resp = await _post_steps(client, workspace_ctx, [step])
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    "step",
    [
        {"type": "tooltip", "selector": "#a"},
        {"type": "hotspot", "selector": "#a"},
        {"type": "modal", "title": "No selector needed"},
        {"type": "banner", "body": "Announcement"},
        {"type": "action", "selector": "#a", "action": {"kind": "click"}},
        {"type": "action", "selector": "#a", "action": {"kind": "fill", "value": "hi"}},
        {
            "type": "action",
            "selector": "#a",
            "action": {"kind": "navigate", "url": "/next"},
        },
        {"type": "wait", "wait": {"for": "element", "selector": "#late"}},
        {"type": "wait", "wait": {"for": "url", "url_pattern": "*/done*"}},
        {"type": "tooltip", "selector": "#a", "advance": {"on": "delay", "delay_ms": 1500}},
        {"type": "tooltip", "selector": "#a", "advance": {"on": "element_click"}},
    ],
)
async def test_valid_steps_accepted(client, workspace_ctx, step):
    resp = await _post_steps(client, workspace_ctx, [step])
    assert resp.status_code == 201, resp.text


async def test_modal_and_banner_ignore_selector_requirement(client, workspace_ctx):
    tour = await create_tour(
        client,
        workspace_ctx,
        steps=[{"type": "modal", "title": "Hello"}, {"type": "banner", "body": "News"}],
    )
    assert [s["type"] for s in tour["steps"]] == ["modal", "banner"]
    assert tour["steps"][0]["selector"] == ""


async def test_action_and_wait_stripped_from_other_types(client, workspace_ctx):
    tour = await create_tour(
        client,
        workspace_ctx,
        steps=[
            {
                "type": "tooltip",
                "selector": "#a",
                "action": {"kind": "click"},
                "wait": {"for": "element", "selector": "#a"},
            }
        ],
    )
    assert tour["steps"][0]["action"] is None
    assert tour["steps"][0]["wait"] is None


async def test_fallback_selectors_capped_at_five(client, workspace_ctx):
    ok = await _post_steps(
        client, workspace_ctx, [{**TOOLTIP, "fallback_selectors": [f"#f{i}" for i in range(5)]}]
    )
    assert ok.status_code == 201
    assert len(ok.json()["steps"][0]["fallback_selectors"]) == 5

    too_many = await _post_steps(
        client, workspace_ctx, [{**TOOLTIP, "fallback_selectors": [f"#f{i}" for i in range(6)]}]
    )
    assert too_many.status_code == 422


async def test_target_passthrough_and_size_cap(client, workspace_ctx):
    target = {
        "selectors": [{"kind": "css", "value": "#a", "score": 0.9}],
        "text": "Save",
        "fingerprint": {"elementHash": "abc", "tagPath": "div>button"},
        "frame": [],
        "shadowPath": [],
    }
    ok = await _post_steps(client, workspace_ctx, [{**TOOLTIP, "target": target}])
    assert ok.status_code == 201
    # Opaque: stored and echoed byte-for-byte.
    assert ok.json()["steps"][0]["target"] == target

    huge = {"blob": "x" * 9000}
    assert (
        await _post_steps(client, workspace_ctx, [{**TOOLTIP, "target": huge}])
    ).status_code == 422


async def test_text_hint_is_clipped(client, workspace_ctx):
    resp = await _post_steps(client, workspace_ctx, [{**TOOLTIP, "text_hint": "y" * 200}])
    assert resp.status_code == 201
    assert resp.json()["steps"][0]["text_hint"] == "y" * 80


async def test_media_round_trips(client, workspace_ctx):
    media = {"type": "video", "url": "https://cdn.example.com/clip.mp4"}
    resp = await _post_steps(client, workspace_ctx, [{**TOOLTIP, "media": media}])
    assert resp.json()["steps"][0]["media"] == media
    bad = await _post_steps(
        client, workspace_ctx, [{**TOOLTIP, "media": {"type": "gif", "url": "x"}}]
    )
    assert bad.status_code == 422


async def test_target_and_screenshot_changes_do_not_bump_version(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx, steps=[TOOLTIP])
    tid = tour["id"]
    assert tour["version"] == 1

    recaptured = await client.patch(
        f"{workspace_ctx.base}/tours/{tid}",
        json={
            "steps": [
                {**TOOLTIP, "target": {"selectors": []}, "screenshot_key": "public/x/shot.png"}
            ]
        },
        headers=workspace_ctx.owner_headers,
    )
    body = recaptured.json()
    assert body["version"] == 1, "re-capture artifacts must not invalidate playback"
    assert body["steps"][0]["screenshot_key"] == "public/x/shot.png"
    assert body["steps"][0]["target"] == {"selectors": []}

    # A content change still bumps.
    changed = await client.patch(
        f"{workspace_ctx.base}/tours/{tid}",
        json={"steps": [{**TOOLTIP, "body": "new copy"}]},
        headers=workspace_ctx.owner_headers,
    )
    assert changed.json()["version"] == 2


async def test_settings_schedule_frequency_round_trip(client, workspace_ctx):
    tour = await create_tour(
        client,
        workspace_ctx,
        kind="banner",
        priority=7,
        settings={"mode": "driven", "backdrop": False, "show_progress": False},
        frequency={"type": "every_time", "cooldown_hours": 12},
        schedule={"start_at": "2026-01-01T00:00:00Z", "end_at": "2026-12-31T00:00:00Z"},
        theme={"accent": "#000000", "position": "bottom"},
    )
    assert tour["kind"] == "banner"
    assert tour["priority"] == 7
    assert tour["settings"] == {
        "mode": "driven",
        "backdrop": False,
        "show_progress": False,
        "dismissable": True,
    }
    assert tour["frequency"] == {"type": "every_time", "cooldown_hours": 12}
    assert tour["schedule"]["start_at"].startswith("2026-01-01")
    assert tour["theme"]["position"] == "bottom"


async def test_defaults_applied_to_bare_step(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx, steps=[{"selector": "#a"}])
    step = tour["steps"][0]
    assert step["type"] == "tooltip"
    assert step["placement"] == "auto"
    assert step["advance"] == {"on": "button", "delay_ms": None}
    assert step["fallback_selectors"] == []
    assert step["id"]
    # Tour-level defaults too.
    assert tour["kind"] == "flow"
    assert tour["frequency"] == {"type": "until_dismissed", "cooldown_hours": None}
    assert tour["settings"]["mode"] == "guided"
