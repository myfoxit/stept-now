"""Crawler hardening: redirect SSRF re-validation, retries/429, User-Agent +
proxy, content-type routing (plain/xhtml/pdf), URL canonicalization +
rel=canonical, success-only page budget, wall-clock budget, robots wildcards /
Crawl-delay / Sitemap discovery."""

from __future__ import annotations

import asyncio

import httpx
import respx

from app.core.config import get_settings
from tests.knowledge.conftest import (
    create_source,
    get_doc,
    get_source_json,
    html_page,
    list_docs,
    robots_response,
    sitemap_response,
    sync_now,
)
from tests.knowledge.test_parsers import build_pdf

BASE = "https://docs.example.com/docs/"
ROBOTS_URL = "https://docs.example.com/robots.txt"


async def crawl_source(client, ctx, **config):
    return await create_source(
        client, ctx, type="crawl", config={"base_url": BASE, "delay_ms": 0, **config}
    )


def page(title, body, links=(), canonical=None, content_type="text/html"):
    head = f"<title>{title}</title>"
    if canonical:
        head += f'<link rel="canonical" href="{canonical}">'
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    content = f"<html><head>{head}</head><body><p>{body}</p>{anchors}</body></html>"
    return httpx.Response(200, content=content.encode(), headers={"content-type": content_type})


def redirect(location, status=302):
    return httpx.Response(status, headers={"location": location})


def quiet_retry_sleeps(monkeypatch):
    """Make retry backoff instant; returns the recorded sleep durations."""
    import app.rag.tasks as tasks

    real_sleep = asyncio.sleep
    slept: list[float] = []

    async def fast(seconds, *args, **kwargs):
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(tasks.asyncio, "sleep", fast)
    return slept


async def doc_chunk_text(client, ctx, document_id):
    detail = await get_doc(client, ctx, document_id)
    return "\n".join(chunk["content"] for chunk in detail["chunks"])


# ---------------------------------------------------------------------------
# redirects: SSRF re-validation, final-URL identity, hop cap
# ---------------------------------------------------------------------------


async def test_redirect_to_private_address_is_blocked(client, workspace_ctx):
    """A public host 302-ing into the cloud metadata range is refused before
    any request is made (respx would fail the test on an unmocked call)."""
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("evil",)))
        respx.get("https://docs.example.com/docs/evil").mock(
            return_value=redirect("http://169.254.169.254/latest/meta-data/")
        )
        await sync_now(client, workspace_ctx, source["id"])

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "idle"  # the home page synced fine
    assert "non-public address" in refreshed["error"]
    assert {d["title"] for d in await list_docs(client, workspace_ctx, source["id"])} == {
        "Docs Home"
    }


async def test_redirect_final_url_becomes_doc_uri_and_joins_visited(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    new_url = "https://docs.example.com/docs/new"
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("old",)))
        respx.get("https://docs.example.com/docs/old").mock(return_value=redirect("/docs/new", 301))
        # The page served at /new links to itself — already visited via the
        # redirect resolution, so it must not be fetched a second time.
        new_route = respx.get(new_url).mock(
            return_value=page("Fresh", "Fresh body.", links=("new",))
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert new_route.call_count == 1
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["uri"] for d in docs} == {BASE, new_url}  # final URL, not the requested one
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_redirect_chains_are_capped(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("hop0",)))
        for index in range(7):
            respx.get(f"https://docs.example.com/docs/hop{index}").mock(
                return_value=redirect(f"/docs/hop{index + 1}")
            )
        await sync_now(client, workspace_ctx, source["id"])

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "idle"
    assert "redirects" in refreshed["error"]


# ---------------------------------------------------------------------------
# retries + 429
# ---------------------------------------------------------------------------


async def test_transient_503_is_retried_and_recovers(client, workspace_ctx, monkeypatch):
    quiet_retry_sleeps(monkeypatch)
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        home = respx.get(BASE).mock(
            side_effect=[httpx.Response(503), html_page("Docs Home", "Recovered.")]
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert home.call_count == 2
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "idle"
    assert refreshed["error"] is None


async def test_timeout_is_retried(client, workspace_ctx, monkeypatch):
    quiet_retry_sleeps(monkeypatch)
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        home = respx.get(BASE).mock(
            side_effect=[httpx.ConnectTimeout("slow"), html_page("Docs Home", "Back.")]
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert home.call_count == 2
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_429_honors_retry_after(client, workspace_ctx, monkeypatch):
    slept = quiet_retry_sleeps(monkeypatch)
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        home = respx.get(BASE).mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "7"}),
                html_page("Docs Home", "Let in."),
            ]
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert home.call_count == 2
    assert 7.0 in slept  # Retry-After honored (the 30s cap is unit-tested in zendesk tests)
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_retries_are_bounded(client, workspace_ctx, monkeypatch):
    quiet_retry_sleeps(monkeypatch)
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        home = respx.get(BASE).mock(return_value=httpx.Response(503))
        await sync_now(client, workspace_ctx, source["id"])

    assert home.call_count == 3  # first try + 2 retries, then give up
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "error"  # nothing fetched at all
    assert "HTTP 503" in refreshed["error"]


async def test_sitemap_listing_fetch_is_retried(client, workspace_ctx, monkeypatch):
    quiet_retry_sleeps(monkeypatch)
    source = await create_source(
        client,
        workspace_ctx,
        type="sitemap",
        config={"sitemap_url": "https://site.example.com/sitemap.xml"},
    )
    with respx.mock:
        listing = respx.get("https://site.example.com/sitemap.xml").mock(
            side_effect=[
                httpx.Response(502),
                sitemap_response(["https://site.example.com/a"]),
            ]
        )
        respx.get("https://site.example.com/a").mock(return_value=html_page("Alpha", "A words."))
        await sync_now(client, workspace_ctx, source["id"])

    assert listing.call_count == 2
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


# ---------------------------------------------------------------------------
# identity: User-Agent + proxy
# ---------------------------------------------------------------------------


async def test_crawler_sends_identifying_user_agent(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        robots = respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        home = respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome."))
        await sync_now(client, workspace_ctx, source["id"])

    expected = get_settings().crawl_user_agent
    assert robots.calls.last.request.headers["user-agent"] == expected
    assert home.calls.last.request.headers["user-agent"] == expected


async def test_urls_source_sends_crawler_user_agent(client, workspace_ctx):
    source = await create_source(
        client, workspace_ctx, type="urls", config={"urls": ["https://site.example.com/page"]}
    )
    with respx.mock:
        route = respx.get("https://site.example.com/page").mock(
            return_value=html_page("Page", "Body.")
        )
        await sync_now(client, workspace_ctx, source["id"])
    assert route.calls.last.request.headers["user-agent"] == get_settings().crawl_user_agent


async def test_crawl_client_routes_through_configured_proxy(monkeypatch):
    import app.rag.tasks as tasks

    settings = get_settings()
    monkeypatch.setattr(settings, "crawl_proxy_url", "http://egress.proxy.internal:3128")
    captured: dict[str, object] = {}
    real_client = httpx.AsyncClient

    def recorder(**kwargs):
        captured.update(kwargs)
        kwargs.pop("proxy", None)  # keep the test client network-free
        return real_client(**kwargs)

    monkeypatch.setattr(tasks.httpx, "AsyncClient", recorder)
    http_client = tasks.crawl_client()
    await http_client.aclose()
    assert captured["proxy"] == "http://egress.proxy.internal:3128"
    assert captured["follow_redirects"] is False  # hops are followed manually
    assert captured["headers"]["user-agent"] == settings.crawl_user_agent


# ---------------------------------------------------------------------------
# content types: plain, xhtml, pdf
# ---------------------------------------------------------------------------


async def test_crawl_indexes_xhtml_pages(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(
            return_value=page(
                "XHTML Home", "Served as xhtml.", content_type="application/xhtml+xml"
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["title"] for d in docs] == ["XHTML Home"]
    assert docs[0]["mime"] == "text/html"
    assert docs[0]["status"] == "indexed"


async def test_crawl_routes_pdfs_through_the_pdf_parser(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    pdf_bytes = build_pdf("Refunds are issued within 30 days.")
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(
            return_value=html_page("Docs Home", "Welcome.", links=("manual.pdf", "spec.pdf"))
        )
        respx.get("https://docs.example.com/docs/manual.pdf").mock(
            return_value=httpx.Response(
                200, content=pdf_bytes, headers={"content-type": "application/pdf"}
            )
        )
        # PDFs are frequently served as octet-stream — the .pdf path decides.
        respx.get("https://docs.example.com/docs/spec.pdf").mock(
            return_value=httpx.Response(
                200,
                content=build_pdf("Spec sheet contents."),
                headers={"content-type": "application/octet-stream"},
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = {d["title"]: d for d in await list_docs(client, workspace_ctx, source["id"])}
    assert set(docs) == {"Docs Home", "manual", "spec"}
    assert docs["manual"]["mime"] == "application/pdf"
    assert docs["spec"]["mime"] == "application/pdf"
    assert docs["manual"]["status"] == "indexed"
    text = await doc_chunk_text(client, workspace_ctx, docs["manual"]["id"])
    assert "Refunds are issued within 30 days." in text
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "idle"
    assert refreshed["error"] is None  # a supported type is not a skip


# ---------------------------------------------------------------------------
# canonicalization + rel=canonical
# ---------------------------------------------------------------------------


def test_normalize_link_folds_case_ports_slashes_and_tracking_params():
    from app.rag.connectors import _normalize_link

    assert (
        _normalize_link("HTTP://Docs.Example.COM:80/a//b?utm_source=x&b=2&a=1#frag")
        == "http://docs.example.com/a/b?a=1&b=2"
    )
    assert _normalize_link("https://x.example.com:443/p") == "https://x.example.com/p"
    assert _normalize_link("https://x.example.com:8443/p") == "https://x.example.com:8443/p"
    assert (
        _normalize_link("https://x.example.com/p?gclid=1&fbclid=2&ref=t&utm_medium=m")
        == "https://x.example.com/p"
    )
    # real query params survive, trailing slash is preserved in the fetch form
    assert _normalize_link("https://x.example.com/p/?tab=1") == "https://x.example.com/p/?tab=1"


def test_visited_key_folds_trailing_slash():
    from app.rag.connectors import _visited_key

    assert _visited_key("https://x.example.com/docs/") == _visited_key("https://x.example.com/docs")
    assert _visited_key("https://x.example.com/") == _visited_key("https://x.example.com")
    assert _visited_key("https://x.example.com/a?b=1") != _visited_key("https://x.example.com/a")


async def test_crawl_dedupes_tracking_and_slash_variants(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(
            return_value=html_page(
                "Docs Home",
                "Welcome.",
                links=("a", "a/", "a?utm_source=newsletter", "b?utm_campaign=x&ref=footer"),
            )
        )
        page_a = respx.get("https://docs.example.com/docs/a").mock(
            return_value=html_page("Alpha", "Alpha body.")
        )
        page_b = respx.get("https://docs.example.com/docs/b").mock(
            return_value=html_page("Beta", "Beta body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert page_a.call_count == 1  # slash + utm variants collapse into one visit
    assert page_b.call_count == 1  # tracking params stripped before the fetch
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert len(docs) == 3
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_same_site_canonical_becomes_doc_identity(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    canonical = "https://docs.example.com/docs/guide"
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("v1", "v2")))
        respx.get("https://docs.example.com/docs/v1").mock(
            return_value=page("Guide", "First variant body.", canonical=canonical)
        )
        respx.get("https://docs.example.com/docs/v2").mock(
            return_value=page("Guide v2", "Second variant body.", canonical=canonical)
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    by_uri = {d["uri"]: d for d in docs}
    # one document under the canonical URI, even though the variants differ
    assert set(by_uri) == {BASE, canonical}
    assert by_uri[canonical]["title"] == "Guide"


async def test_offsite_canonical_is_ignored(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(
            return_value=page(
                "Docs Home", "Welcome.", canonical="https://cdn.other.example.com/mirror"
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["uri"] for d in docs] == [BASE]  # cross-site canonical never wins


# ---------------------------------------------------------------------------
# budgets: pages + wall clock
# ---------------------------------------------------------------------------


async def test_failed_fetches_do_not_consume_the_page_budget(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx, max_pages=3)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(
            return_value=html_page("Docs Home", "Welcome.", links=("bad", "ok1", "ok2"))
        )
        respx.get("https://docs.example.com/docs/bad").mock(return_value=httpx.Response(404))
        respx.get("https://docs.example.com/docs/ok1").mock(
            return_value=html_page("OK1", "One body.")
        )
        ok2 = respx.get("https://docs.example.com/docs/ok2").mock(
            return_value=html_page("OK2", "Two body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert ok2.called  # the 404 left budget for the next link
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"Docs Home", "OK1", "OK2"}
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "idle"
    assert "404" in refreshed["error"]


async def test_wall_clock_budget_stops_gracefully_and_blocks_pruning(
    client, workspace_ctx, monkeypatch
):
    import app.rag.connectors as connectors

    source = await crawl_source(client, workspace_ctx)
    with respx.mock:  # first sync: full crawl
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("child",)))
        respx.get("https://docs.example.com/docs/child").mock(
            return_value=html_page("Child", "Child body.")
        )
        await sync_now(client, workspace_ctx, source["id"])
    assert len(await list_docs(client, workspace_ctx, source["id"])) == 2

    # Second sync with an exhausted budget: the seed level is fetched, the
    # frontier is dropped, and — because the listing is incomplete — nothing
    # is pruned.
    monkeypatch.setattr(connectors, "CRAWL_TIME_BUDGET_SECONDS", 0.0)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("child",)))
        child = respx.get("https://docs.example.com/docs/child").mock(
            return_value=html_page("Child", "Child body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert not child.called  # frontier abandoned after the budget ran out
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"Docs Home", "Child"}  # child kept — no pruning
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


# ---------------------------------------------------------------------------
# partial success: errored pages keep their docs; unlinked docs still prune
# ---------------------------------------------------------------------------


async def test_crawl_keeps_fetch_failed_docs_but_prunes_unlinked_ones(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:  # sync 1: home → a, b
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("a", "b")))
        respx.get("https://docs.example.com/docs/a").mock(
            return_value=html_page("Alpha", "Alpha body.")
        )
        respx.get("https://docs.example.com/docs/b").mock(
            return_value=html_page("Beta", "Beta body.")
        )
        await sync_now(client, workspace_ctx, source["id"])
    assert len(await list_docs(client, workspace_ctx, source["id"])) == 3

    with respx.mock:  # sync 2: b still linked but 404s → kept, marked failed
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("a", "b")))
        respx.get("https://docs.example.com/docs/a").mock(
            return_value=html_page("Alpha", "Alpha body.")
        )
        respx.get("https://docs.example.com/docs/b").mock(return_value=httpx.Response(404))
        await sync_now(client, workspace_ctx, source["id"])

    docs = {d["uri"]: d for d in await list_docs(client, workspace_ctx, source["id"])}
    assert len(docs) == 3  # the failing page's doc survives
    assert docs["https://docs.example.com/docs/b"]["status"] == "failed"
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"

    with respx.mock:  # sync 3: b no longer linked anywhere → pruned
        respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome.", links=("a",)))
        respx.get("https://docs.example.com/docs/a").mock(
            return_value=html_page("Alpha", "Alpha body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    uris = {d["uri"] for d in await list_docs(client, workspace_ctx, source["id"])}
    assert uris == {BASE, "https://docs.example.com/docs/a"}


# ---------------------------------------------------------------------------
# robots: Crawl-delay + Sitemap discovery (wildcard matching is unit-tested in
# test_crawl_advanced)
# ---------------------------------------------------------------------------


async def test_robots_crawl_delay_overrides_smaller_configured_delay(
    client, workspace_ctx, monkeypatch
):
    import app.rag.connectors as connectors

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(connectors.asyncio, "sleep", fake_sleep)
    source = await crawl_source(client, workspace_ctx)  # delay_ms=0
    with respx.mock:
        respx.get(ROBOTS_URL).mock(return_value=robots_response("User-agent: *\nCrawl-delay: 3\n"))
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome."))
        await sync_now(client, workspace_ctx, source["id"])

    assert slept == [3.0]  # robots Crawl-delay applied to the page fetch
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_robots_sitemap_seeds_the_frontier(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx)
    with respx.mock:
        respx.get(ROBOTS_URL).mock(
            return_value=robots_response(
                "User-agent: *\nDisallow:\nSitemap: https://docs.example.com/sitemap.xml\n"
            )
        )
        sitemap = respx.get("https://docs.example.com/sitemap.xml").mock(
            return_value=sitemap_response(
                [
                    "https://docs.example.com/docs/orphan",  # not linked from any page
                    "https://docs.example.com/pricing",  # outside the base path
                    "https://other.example.com/offsite",  # off-site
                ]
            )
        )
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome."))
        orphan = respx.get("https://docs.example.com/docs/orphan").mock(
            return_value=html_page("Orphan", "Findable only via the sitemap.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert sitemap.call_count == 1
    assert orphan.called  # merged into the depth-1 frontier
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"Docs Home", "Orphan"}
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_robots_sitemap_is_ignored_when_include_patterns_are_set(client, workspace_ctx):
    source = await crawl_source(client, workspace_ctx, include_patterns=["/docs/*"])
    with respx.mock:
        respx.get(ROBOTS_URL).mock(
            return_value=robots_response(
                "User-agent: *\nDisallow:\nSitemap: https://docs.example.com/sitemap.xml\n"
            )
        )
        sitemap = respx.get("https://docs.example.com/sitemap.xml").mock(
            return_value=sitemap_response(["https://docs.example.com/docs/orphan"])
        )
        respx.get(BASE).mock(return_value=html_page("Docs Home", "Welcome."))
        await sync_now(client, workspace_ctx, source["id"])

    assert not sitemap.called  # an explicit include list disables sitemap seeding
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["title"] for d in docs] == ["Docs Home"]
