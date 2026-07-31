"""Crawl upgrades: robots.txt, include/exclude globs, delay + concurrency."""

from __future__ import annotations

import httpx
import respx

from app.rag.connectors import parse_robots
from tests.knowledge.conftest import (
    create_source,
    get_source_json,
    html_page,
    list_docs,
    robots_response,
    sync_now,
)

BASE = "https://docs.example.com/docs/"
ROBOTS_URL = "https://docs.example.com/robots.txt"


async def crawl_source(client, ctx, **config):
    """A crawl source over BASE with the politeness delay disabled."""
    return await create_source(
        client, ctx, type="crawl", config={"base_url": BASE, "delay_ms": 0, **config}
    )


# ---------------------------------------------------------------------------
# robots.txt parsing (pure)
# ---------------------------------------------------------------------------


def test_robots_disallow_blocks_matching_prefixes():
    rules = parse_robots("User-agent: *\nDisallow: /docs/private\n")
    assert rules.allows("https://docs.example.com/docs/public") is True
    assert rules.allows("https://docs.example.com/docs/private") is False
    assert rules.allows("https://docs.example.com/docs/private/deep") is False


def test_robots_longest_match_wins_and_allow_beats_disallow_on_ties():
    rules = parse_robots(
        "User-agent: *\nDisallow: /docs/\nAllow: /docs/public/\nDisallow: /docs/public/secret\n"
    )
    assert rules.allows("https://docs.example.com/docs/anything") is False
    assert rules.allows("https://docs.example.com/docs/public/guide") is True  # longer Allow
    assert rules.allows("https://docs.example.com/docs/public/secret") is False  # longer again

    tie = parse_robots("User-agent: *\nDisallow: /docs\nAllow: /docs\n")
    assert tie.allows("https://docs.example.com/docs/x") is True  # Allow wins equal length
    reversed_tie = parse_robots("User-agent: *\nAllow: /docs\nDisallow: /docs\n")
    assert reversed_tie.allows("https://docs.example.com/docs/x") is True  # order-independent


def test_robots_only_reads_the_star_group():
    rules = parse_robots(
        "User-agent: googlebot\nDisallow: /docs/\n\nUser-agent: *\nDisallow: /docs/private\n"
    )
    assert rules.allows("https://docs.example.com/docs/guide") is True
    assert rules.allows("https://docs.example.com/docs/private") is False

    grouped = parse_robots("User-agent: googlebot\nUser-agent: *\nDisallow: /docs/private\n")
    assert grouped.allows("https://docs.example.com/docs/private") is False


def test_robots_missing_empty_and_malformed_bodies_allow_everything():
    for body in (
        "",  # missing/empty robots.txt
        "User-agent: *\nDisallow:\n",  # empty Disallow == allow all
        "<html><body>Not a robots file</body></html>",
        "\x00\x01 garbage ]][[",
        "Disallow: /docs/private",  # directive with no User-agent group
        "User-agent: bot\nDisallow: /docs/private",  # different agent only
    ):
        rules = parse_robots(body)
        assert rules.allows("https://docs.example.com/docs/private") is True, body


def test_robots_ignores_comments_and_casing():
    rules = parse_robots("# comment\nUSER-AGENT: *\nDISALLOW: /docs/private  # inline comment\n")
    assert rules.allows("https://docs.example.com/docs/private") is False


# ---------------------------------------------------------------------------
# robots.txt during a crawl
# ---------------------------------------------------------------------------


async def test_crawl_skips_robots_disallowed_urls(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        robots = respx.get(ROBOTS_URL).mock(
            return_value=robots_response("User-agent: *\nDisallow: /docs/private\n")
        )
        respx.get(BASE).mock(
            return_value=html_page("Docs Home", "Welcome.", links=("public", "private/secret"))
        )
        public = respx.get("https://docs.example.com/docs/public").mock(
            return_value=html_page("Public", "Public body.")
        )
        private = respx.get("https://docs.example.com/docs/private/secret").mock(
            return_value=html_page("Secret", "Secret body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert robots.call_count == 1  # fetched once per sync
    assert public.called
    assert not private.called
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"Docs Home", "Public"}
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_crawl_respect_robots_false_skips_the_fetch_entirely(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx, respect_robots=False)
    with respx.mock:
        robots = respx.get(ROBOTS_URL).mock(
            return_value=robots_response("User-agent: *\nDisallow: /docs/\n")
        )
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("private",)))
        private = respx.get("https://docs.example.com/docs/private").mock(
            return_value=html_page("Private", "Private body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert not robots.called
    assert private.called
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"Docs Home", "Private"}


async def test_crawl_treats_unreachable_robots_as_allow_all(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(500, content=b"boom"))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("a",)))
        respx.get("https://docs.example.com/docs/a").mock(
            return_value=html_page("Alpha", "Alpha body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"Docs Home", "Alpha"}
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


# ---------------------------------------------------------------------------
# include/exclude globs
# ---------------------------------------------------------------------------


async def test_crawl_exclude_patterns_drop_links_and_beat_include(client, workspace_ctx):
    source = await crawl_source(
        client,
        workspace_ctx,
        include_patterns=["/docs/guide/*"],
        exclude_patterns=["*/changelog*", "/docs/guide/legacy*"],
    )
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(
            return_value=html_page(
                "Docs Home",
                "Welcome.",
                links=("guide/intro", "guide/legacy-v1", "changelog", "api/reference"),
            )
        )
        intro = respx.get("https://docs.example.com/docs/guide/intro").mock(
            return_value=html_page("Intro", "Intro body.")
        )
        legacy = respx.get("https://docs.example.com/docs/guide/legacy-v1").mock(
            return_value=html_page("Legacy", "Legacy body.")
        )
        changelog = respx.get("https://docs.example.com/docs/changelog").mock(
            return_value=html_page("Changelog", "Changelog body.")
        )
        api = respx.get("https://docs.example.com/docs/api/reference").mock(
            return_value=html_page("API", "API body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert intro.called
    assert not legacy.called  # excluded even though it matches the include glob
    assert not changelog.called  # excluded
    assert not api.called  # not in the include list
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"Docs Home", "Intro"}


async def test_crawl_without_patterns_follows_every_same_site_link(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(
            return_value=html_page("Docs Home", "Welcome.", links=("guide/intro", "changelog"))
        )
        respx.get("https://docs.example.com/docs/guide/intro").mock(
            return_value=html_page("Intro", "Intro body.")
        )
        respx.get("https://docs.example.com/docs/changelog").mock(
            return_value=html_page("Changelog", "Changelog body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"Docs Home", "Intro", "Changelog"}


async def test_crawl_pattern_config_is_validated_and_clamped(client, workspace_ctx):
    for config in (
        {"base_url": BASE, "include_patterns": "not-a-list"},
        {"base_url": BASE, "exclude_patterns": [123]},
        {"base_url": BASE, "include_patterns": ["x" * 201]},
        {"base_url": BASE, "exclude_patterns": [f"/p{i}/*" for i in range(21)]},
        {"base_url": BASE, "respect_robots": "yes"},
        {"base_url": BASE, "delay_ms": -1},
        {"base_url": BASE, "delay_ms": "slow"},
    ):
        response = await client.post(
            f"{workspace_ctx.base}/knowledge/sources",
            json={"type": "crawl", "name": "C", "config": config},
            headers=workspace_ctx.owner_headers,
        )
        assert response.status_code == 422, (config, response.text)

    ok = await create_source(
        client,
        workspace_ctx,
        type="crawl",
        config={
            "base_url": BASE,
            "delay_ms": 9999,
            "include_patterns": ["/docs/*", "  ", " /docs/api/* "],
            "exclude_patterns": [],
            "respect_robots": False,
        },
    )
    assert ok["config"]["delay_ms"] == 2000  # clamped to the ceiling
    assert ok["config"]["include_patterns"] == ["/docs/*", "/docs/api/*"]  # trimmed, blanks gone
    assert ok["config"]["respect_robots"] is False


# ---------------------------------------------------------------------------
# concurrency, caps, error collection
# ---------------------------------------------------------------------------


async def test_crawl_fetches_a_whole_depth_level_and_respects_max_pages(client, workspace_ctx):
    """Six links at depth 1 are fetched concurrently under the semaphore; the
    max_pages cap still counts every fetched page (home + 4 children)."""
    source = await crawl_source(client, workspace_ctx, max_pages=5)
    children = [f"p{i}" for i in range(6)]
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=children))
        routes = {
            child: respx.get(f"https://docs.example.com/docs/{child}").mock(
                return_value=html_page(child.upper(), f"Body of {child}.")
            )
            for child in children
        }
        await sync_now(client, workspace_ctx, source["id"])

    fetched = [child for child, route in routes.items() if route.called]
    assert fetched == children[:4]  # frontier order preserved, capped at max_pages
    assert all(route.call_count <= 1 for route in routes.values())
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert len(docs) == 5
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_crawl_collects_per_page_errors_without_losing_good_pages(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(
            return_value=html_page("Docs Home", "Welcome.", links=("ok", "missing", "plain"))
        )
        respx.get("https://docs.example.com/docs/ok").mock(
            return_value=html_page("Okay", "Okay body.")
        )
        respx.get("https://docs.example.com/docs/missing").mock(
            return_value=httpx.Response(404, content=b"nope")
        )
        respx.get("https://docs.example.com/docs/plain").mock(
            return_value=httpx.Response(
                200, content=b"just text", headers={"content-type": "text/plain"}
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "error"
    assert "/docs/missing: HTTP 404" in refreshed["error"]
    assert "text/plain" in refreshed["error"]
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"Docs Home", "Okay"}  # good pages still indexed


async def test_crawl_still_enforces_the_two_megabyte_page_cap(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("huge",)))
        respx.get("https://docs.example.com/docs/huge").mock(
            return_value=httpx.Response(
                200,
                content=b"<html>" + b"x" * (2 * 1024 * 1024 + 100),
                headers={"content-type": "text/html"},
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "error"
    assert "2MB" in refreshed["error"]
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["title"] for d in docs] == ["Docs Home"]


async def test_crawl_delay_is_applied_between_fetches(client, workspace_ctx, monkeypatch):
    """The politeness delay sleeps once per fetched page (value, not timing)."""
    import app.rag.connectors as connectors

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(connectors.asyncio, "sleep", fake_sleep)
    source = await create_source(
        client, workspace_ctx, type="crawl", config={"base_url": BASE, "delay_ms": 400}
    )
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("a", "b")))
        respx.get("https://docs.example.com/docs/a").mock(return_value=html_page("A", "A body."))
        respx.get("https://docs.example.com/docs/b").mock(return_value=html_page("B", "B body."))
        await sync_now(client, workspace_ctx, source["id"])

    assert slept == [0.4, 0.4, 0.4]  # one pause per page fetch, robots excluded
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert len(docs) == 3
