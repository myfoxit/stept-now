"""Banner presentation theme + per-step CTAs (v2.1 authoring surface).

These fields land on the customer's own page as inline CSS and `href`s, so the
tests here care as much about what is *rejected* as what round-trips.
"""

from __future__ import annotations

import pytest

from tests.tours.conftest import create_tour

TOOLTIP = {"type": "tooltip", "selector": "#a", "title": "T"}

FULL_BANNER_THEME = {
    "layout": "inline",
    "full_width": False,
    "max_width": 720,
    "align": "center",
    "background": "#0f172a",
    "text_color": "#f8fafc",
    "icon": "🎉",
    "dismiss": "never_again",
    "rounded": True,
}


async def _patch(client, ctx, tour_id, body):
    return await client.patch(f"{ctx.base}/tours/{tour_id}", json=body, headers=ctx.owner_headers)


# --- banner theme -----------------------------------------------------------


async def test_banner_theme_round_trips(client, workspace_ctx):
    tour = await create_tour(
        client,
        workspace_ctx,
        kind="banner",
        theme={"accent": "#6366f1", "position": "top", "banner": FULL_BANNER_THEME},
    )
    assert tour["theme"]["position"] == "top"
    assert tour["theme"]["banner"] == FULL_BANNER_THEME


async def test_banner_theme_defaults_reproduce_the_legacy_bar(client, workspace_ctx):
    """A tour saved before these fields existed must render identically."""
    tour = await create_tour(client, workspace_ctx, theme={"accent": "#123456"})
    banner = tour["theme"]["banner"]
    assert banner == {
        "layout": "overlay",
        "full_width": True,
        "max_width": None,
        "align": "start",
        "background": None,
        "text_color": None,
        "icon": None,
        "dismiss": "dismiss",
        "rounded": False,
    }


@pytest.mark.parametrize(
    "banner",
    [
        {"background": "red; } body { display:none"},  # CSS declaration escape
        {"text_color": "url(javascript:alert(1))"},
        {"background": "expression(alert(1))"},
        {"layout": "sidebar"},
        {"align": "justify"},
        {"dismiss": "forever"},
        {"max_width": 10},  # below the floor
        {"max_width": 9000},  # above the ceiling
    ],
)
async def test_invalid_banner_theme_rejected(client, workspace_ctx, banner):
    resp = await client.post(
        f"{workspace_ctx.base}/tours",
        json={"name": "B", "steps": [], "theme": {"banner": banner}},
        headers=workspace_ctx.owner_headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    "color", ["#fff", "#ffffff", "#ffffffcc", "rgb(15, 23, 42)", "rgba(15,23,42,0.5)", "tomato"]
)
async def test_accepted_colour_literals(client, workspace_ctx, color):
    tour = await create_tour(client, workspace_ctx, theme={"banner": {"background": color}})
    assert tour["theme"]["banner"]["background"] == color


async def test_blank_colour_normalizes_to_none(client, workspace_ctx):
    """The colour inputs clear to "", which must mean "inherit the accent"
    rather than a CSS value of empty string."""
    tour = await create_tour(
        client, workspace_ctx, theme={"banner": {"background": "", "text_color": "", "icon": "  "}}
    )
    assert tour["theme"]["banner"]["background"] is None
    assert tour["theme"]["banner"]["text_color"] is None
    assert tour["theme"]["banner"]["icon"] is None


async def test_banner_theme_survives_a_steps_only_patch(client, workspace_ctx):
    tour = await create_tour(
        client, workspace_ctx, kind="banner", theme={"banner": {"layout": "inline"}}
    )
    patched = await _patch(client, workspace_ctx, tour["id"], {"steps": [TOOLTIP]})
    assert patched.status_code == 200, patched.text
    assert patched.json()["theme"]["banner"]["layout"] == "inline"


# --- step CTAs --------------------------------------------------------------


async def test_step_ctas_round_trip(client, workspace_ctx):
    step = {
        **TOOLTIP,
        "cta": {"label": "Start setup", "url": "https://example.com/setup"},
        "secondary_cta": {"label": "Later", "url": None},
    }
    tour = await create_tour(client, workspace_ctx, steps=[step])
    saved = tour["steps"][0]
    assert saved["cta"] == {"label": "Start setup", "url": "https://example.com/setup"}
    assert saved["secondary_cta"] == {"label": "Later", "url": None}


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
    ],
)
async def test_dangerous_cta_schemes_rejected(client, workspace_ctx, url):
    resp = await client.post(
        f"{workspace_ctx.base}/tours",
        json={"name": "X", "steps": [{**TOOLTIP, "cta": {"label": "Go", "url": url}}]},
        headers=workspace_ctx.owner_headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    "url", ["https://example.com", "http://example.com/x?y=1", "mailto:a@b.co", "/relative/path"]
)
async def test_safe_cta_urls_accepted(client, workspace_ctx, url):
    tour = await create_tour(
        client, workspace_ctx, steps=[{**TOOLTIP, "cta": {"label": "Go", "url": url}}]
    )
    assert tour["steps"][0]["cta"]["url"] == url


async def test_empty_cta_collapses_to_none(client, workspace_ctx):
    """An untouched CTA row in the editor must not persist as an empty button
    (nor count as a content change that bumps the version)."""
    tour = await create_tour(
        client, workspace_ctx, steps=[{**TOOLTIP, "cta": {"label": "  ", "url": ""}}]
    )
    assert tour["steps"][0]["cta"] is None
    assert tour["version"] == 1

    unchanged = await _patch(
        client, workspace_ctx, tour["id"], {"steps": [{**TOOLTIP, "cta": {"label": ""}}]}
    )
    assert unchanged.json()["version"] == 1


async def test_cta_change_bumps_the_version(client, workspace_ctx):
    tour = await create_tour(client, workspace_ctx, steps=[TOOLTIP])
    changed = await _patch(
        client,
        workspace_ctx,
        tour["id"],
        {"steps": [{**TOOLTIP, "cta": {"label": "Do it", "url": None}}]},
    )
    assert changed.json()["version"] == 2


# --- sandbox key ------------------------------------------------------------


async def test_sandbox_key_round_trips_without_bumping_version(client, workspace_ctx):
    """Like `screenshot_key`, a re-capture must not restart in-flight tours."""
    tour = await create_tour(client, workspace_ctx, steps=[TOOLTIP])
    assert tour["steps"][0]["sandbox_key"] is None

    captured = await _patch(
        client,
        workspace_ctx,
        tour["id"],
        {"steps": [{**TOOLTIP, "sandbox_key": "public/w/2026/08/abc-snapshot.json"}]},
    )
    body = captured.json()
    assert body["steps"][0]["sandbox_key"] == "public/w/2026/08/abc-snapshot.json"
    assert body["version"] == 1


async def test_banner_theme_and_ctas_reach_the_widget_payload(client, workspace_ctx):
    """The player needs both to draw the bar — a public projection that drops
    them would silently fall back to the default styling on customer sites."""
    from tests.tours.conftest import widget_key_for

    tour = await create_tour(
        client,
        workspace_ctx,
        kind="banner",
        theme={"accent": "#111111", "position": "top", "banner": FULL_BANNER_THEME},
        steps=[{"type": "banner", "body": "News", "cta": {"label": "Read", "url": "/blog"}}],
        trigger={"type": "url_match", "url_pattern": "*"},
    )
    await client.post(
        f"{workspace_ctx.base}/tours/{tour['id']}/publish", headers=workspace_ctx.owner_headers
    )

    key = await widget_key_for(client, workspace_ctx)
    resp = await client.get(
        "/api/widget/experiences", params={"widget_key": key, "url": "https://app.example.com/x"}
    )
    assert resp.status_code == 200, resp.text
    delivered = [t for t in resp.json()["tours"] if t["id"] == tour["id"]]
    assert delivered, "published banner tour was not delivered"
    assert delivered[0]["theme"]["banner"] == FULL_BANNER_THEME
    assert delivered[0]["steps"][0]["cta"] == {"label": "Read", "url": "/blog"}
