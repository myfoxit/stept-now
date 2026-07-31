"""Sitemap incremental sync: <lastmod> stored, unchanged URLs skipped."""

from __future__ import annotations

import httpx
import respx

from tests.knowledge.conftest import (
    create_source,
    get_doc,
    get_source_json,
    html_page,
    list_docs,
    sitemap_response,
    sync_now,
)

SITEMAP = "https://site.example.com/sitemap.xml"
PAGE_A = "https://site.example.com/a"
PAGE_B = "https://site.example.com/b"


async def sitemap_source(client, ctx):
    return await create_source(client, ctx, type="sitemap", config={"sitemap_url": SITEMAP})


async def test_lastmod_is_stored_and_unchanged_urls_are_not_refetched(client, workspace_ctx):
    source = await sitemap_source(client, workspace_ctx)
    listing = [(PAGE_A, "2026-07-01"), (PAGE_B, "2026-07-02")]
    with respx.mock:
        respx.get(SITEMAP).mock(return_value=sitemap_response(listing))
        respx.get(PAGE_A).mock(return_value=html_page("Alpha", "Alpha facts."))
        respx.get(PAGE_B).mock(return_value=html_page("Beta", "Beta facts."))
        await sync_now(client, workspace_ctx, source["id"])

    docs = {d["uri"]: d for d in await list_docs(client, workspace_ctx, source["id"])}
    assert docs[PAGE_A]["meta"]["lastmod"] == "2026-07-01"
    assert docs[PAGE_B]["meta"]["lastmod"] == "2026-07-02"
    assert all(d["status"] == "indexed" for d in docs.values())

    with respx.mock:  # identical stamps → the pages are never requested again
        respx.get(SITEMAP).mock(return_value=sitemap_response(listing))
        page_a = respx.get(PAGE_A).mock(return_value=html_page("Alpha", "Should not be fetched."))
        page_b = respx.get(PAGE_B).mock(return_value=html_page("Beta", "Should not be fetched."))
        await sync_now(client, workspace_ctx, source["id"])
        assert not page_a.called
        assert not page_b.called

    # skipped pages count as present: nothing is pruned, ids/content survive
    after = {d["uri"]: d for d in await list_docs(client, workspace_ctx, source["id"])}
    assert set(after) == {PAGE_A, PAGE_B}
    assert after[PAGE_A]["id"] == docs[PAGE_A]["id"]
    assert after[PAGE_A]["content_hash"] == docs[PAGE_A]["content_hash"]
    assert after[PAGE_A]["status"] == "indexed"
    detail = await get_doc(client, workspace_ctx, docs[PAGE_A]["id"])
    assert "Alpha facts." in "\n".join(chunk["content"] for chunk in detail["chunks"])
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_changed_lastmod_refetches_only_that_url(client, workspace_ctx):
    source = await sitemap_source(client, workspace_ctx)
    with respx.mock:
        respx.get(SITEMAP).mock(
            return_value=sitemap_response([(PAGE_A, "2026-07-01"), (PAGE_B, "2026-07-02")])
        )
        respx.get(PAGE_A).mock(return_value=html_page("Alpha", "Old alpha."))
        respx.get(PAGE_B).mock(return_value=html_page("Beta", "Beta facts."))
        await sync_now(client, workspace_ctx, source["id"])

    with respx.mock:  # only /a's stamp moved
        respx.get(SITEMAP).mock(
            return_value=sitemap_response([(PAGE_A, "2026-07-09"), (PAGE_B, "2026-07-02")])
        )
        page_a = respx.get(PAGE_A).mock(return_value=html_page("Alpha v2", "Fresh alpha."))
        page_b = respx.get(PAGE_B).mock(return_value=html_page("Beta", "Beta facts."))
        await sync_now(client, workspace_ctx, source["id"])
        assert page_a.call_count == 1
        assert not page_b.called

    docs = {d["uri"]: d for d in await list_docs(client, workspace_ctx, source["id"])}
    assert docs[PAGE_A]["title"] == "Alpha v2"
    assert docs[PAGE_A]["meta"]["lastmod"] == "2026-07-09"
    detail = await get_doc(client, workspace_ctx, docs[PAGE_A]["id"])
    assert "Fresh alpha." in "\n".join(chunk["content"] for chunk in detail["chunks"])


async def test_missing_lastmod_always_refetches(client, workspace_ctx):
    source = await sitemap_source(client, workspace_ctx)
    listing = [PAGE_A, (PAGE_B, "2026-07-02")]  # /a has no <lastmod>
    with respx.mock:
        respx.get(SITEMAP).mock(return_value=sitemap_response(listing))
        respx.get(PAGE_A).mock(return_value=html_page("Alpha", "Alpha facts."))
        respx.get(PAGE_B).mock(return_value=html_page("Beta", "Beta facts."))
        await sync_now(client, workspace_ctx, source["id"])

    docs = {d["uri"]: d for d in await list_docs(client, workspace_ctx, source["id"])}
    assert "lastmod" not in docs[PAGE_A]["meta"]

    with respx.mock:
        respx.get(SITEMAP).mock(return_value=sitemap_response(listing))
        page_a = respx.get(PAGE_A).mock(return_value=html_page("Alpha", "Alpha facts."))
        page_b = respx.get(PAGE_B).mock(return_value=html_page("Beta", "Beta facts."))
        await sync_now(client, workspace_ctx, source["id"])
        assert page_a.call_count == 1  # no stamp → always refetched
        assert not page_b.called


async def test_failed_document_is_refetched_even_when_lastmod_matches(client, workspace_ctx):
    source = await sitemap_source(client, workspace_ctx)
    listing = [(PAGE_A, "2026-07-01")]
    with respx.mock:
        respx.get(SITEMAP).mock(return_value=sitemap_response(listing))
        respx.get(PAGE_A).mock(return_value=httpx.Response(500, content=b"boom"))
        await sync_now(client, workspace_ctx, source["id"])
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "error"

    with respx.mock:  # same stamp, but the document never indexed → retry it
        respx.get(SITEMAP).mock(return_value=sitemap_response(listing))
        page_a = respx.get(PAGE_A).mock(return_value=html_page("Alpha", "Alpha facts."))
        await sync_now(client, workspace_ctx, source["id"])
        assert page_a.call_count == 1

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["status"] for d in docs] == ["indexed"]
    assert docs[0]["meta"]["lastmod"] == "2026-07-01"


async def test_urls_dropped_from_the_listing_are_still_pruned(client, workspace_ctx):
    source = await sitemap_source(client, workspace_ctx)
    with respx.mock:
        respx.get(SITEMAP).mock(
            return_value=sitemap_response([(PAGE_A, "2026-07-01"), (PAGE_B, "2026-07-02")])
        )
        respx.get(PAGE_A).mock(return_value=html_page("Alpha", "Alpha facts."))
        respx.get(PAGE_B).mock(return_value=html_page("Beta", "Beta facts."))
        await sync_now(client, workspace_ctx, source["id"])

    with respx.mock:  # /b vanished; /a is unchanged and therefore skipped
        respx.get(SITEMAP).mock(return_value=sitemap_response([(PAGE_A, "2026-07-01")]))
        page_a = respx.get(PAGE_A).mock(return_value=html_page("Alpha", "Alpha facts."))
        await sync_now(client, workspace_ctx, source["id"])
        assert not page_a.called

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["uri"] for d in docs] == [PAGE_A]
