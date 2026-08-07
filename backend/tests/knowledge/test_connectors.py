"""Connector sources: sitemap, crawl, GitHub, Notion — sync, pruning, secrets."""

from __future__ import annotations

import base64
import json

import httpx
import respx

from tests.conftest import bearer, drain_tasks, signup

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def create_source(
    client, ctx, *, type, name="Connector", config=None, secrets=None, headers=None
):
    body = {"type": type, "name": name, "config": config or {}}
    if secrets is not None:
        body["secrets"] = secrets
    response = await client.post(
        f"{ctx.base}/knowledge/sources", json=body, headers=headers or ctx.owner_headers
    )
    assert response.status_code == 201, response.text
    return response.json()


async def sync_now(client, ctx, source_id):
    response = await client.post(
        f"{ctx.base}/knowledge/sources/{source_id}/sync", headers=ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    await drain_tasks()


async def get_source_json(client, ctx, source_id):
    response = await client.get(
        f"{ctx.base}/knowledge/sources/{source_id}", headers=ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def list_docs(client, ctx, source_id):
    response = await client.get(
        f"{ctx.base}/knowledge/documents?source_id={source_id}&limit=50",
        headers=ctx.owner_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def doc_text(client, ctx, document_id):
    response = await client.get(
        f"{ctx.base}/knowledge/documents/{document_id}", headers=ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    return "\n".join(chunk["content"] for chunk in response.json()["chunks"])


def html_page(title, body, links=()):
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    content = f"<html><head><title>{title}</title></head><body><p>{body}</p>{anchors}</body></html>"
    return httpx.Response(200, content=content.encode(), headers={"content-type": "text/html"})


def sitemap_response(urls):
    locs = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="{SITEMAP_NS}">{locs}</urlset>'
    return httpx.Response(200, content=xml.encode(), headers={"content-type": "application/xml"})


def sitemap_index_response(sitemap_urls):
    locs = "".join(f"<sitemap><loc>{url}</loc></sitemap>" for url in sitemap_urls)
    xml = f'<?xml version="1.0"?><sitemapindex xmlns="{SITEMAP_NS}">{locs}</sitemapindex>'
    return httpx.Response(200, content=xml.encode(), headers={"content-type": "application/xml"})


# ---------------------------------------------------------------------------
# sitemap
# ---------------------------------------------------------------------------


async def test_sitemap_sync_ingests_listed_pages(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="sitemap",
        config={"sitemap_url": "https://site.example.com/sitemap.xml"},
    )
    with respx.mock:
        respx.get("https://site.example.com/sitemap.xml").mock(
            return_value=sitemap_response(
                ["https://site.example.com/a", "https://site.example.com/b"]
            )
        )
        respx.get("https://site.example.com/a").mock(
            return_value=html_page("Alpha", "Alpha page facts.")
        )
        respx.get("https://site.example.com/b").mock(
            return_value=html_page("Beta", "Beta page facts.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["uri"] for d in docs} == {
        "https://site.example.com/a",
        "https://site.example.com/b",
    }
    assert all(d["status"] == "indexed" for d in docs)
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "idle"
    assert refreshed["last_synced_at"]


async def test_sitemap_index_recurses_into_child_sitemaps(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="sitemap",
        config={"sitemap_url": "https://site.example.com/sitemap_index.xml"},
    )
    with respx.mock:
        respx.get("https://site.example.com/sitemap_index.xml").mock(
            return_value=sitemap_index_response(
                ["https://site.example.com/s1.xml", "https://site.example.com/s2.xml"]
            )
        )
        respx.get("https://site.example.com/s1.xml").mock(
            return_value=sitemap_response(["https://site.example.com/a"])
        )
        respx.get("https://site.example.com/s2.xml").mock(
            return_value=sitemap_response(["https://site.example.com/b"])
        )
        respx.get("https://site.example.com/a").mock(return_value=html_page("Alpha", "A words."))
        respx.get("https://site.example.com/b").mock(return_value=html_page("Beta", "B words."))
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert len(docs) == 2
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_sitemap_caps_pages_at_max_pages(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="sitemap",
        config={"sitemap_url": "https://site.example.com/sitemap.xml", "max_pages": 2},
    )
    with respx.mock:
        respx.get("https://site.example.com/sitemap.xml").mock(
            return_value=sitemap_response([f"https://site.example.com/p{i}" for i in range(4)])
        )
        # only the first two pages are mocked — fetching p2/p3 would blow up
        respx.get("https://site.example.com/p0").mock(return_value=html_page("P0", "Zero."))
        respx.get("https://site.example.com/p1").mock(return_value=html_page("P1", "One."))
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert len(docs) == 2
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_sitemap_non_xml_marks_source_errored(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="sitemap",
        config={"sitemap_url": "https://site.example.com/sitemap.xml"},
    )
    with respx.mock:
        respx.get("https://site.example.com/sitemap.xml").mock(
            return_value=httpx.Response(
                200, content=b"this is not a sitemap", headers={"content-type": "text/html"}
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "error"
    assert "XML" in refreshed["error"]
    assert refreshed["last_synced_at"]


async def test_sitemap_resync_prunes_documents_removed_from_listing(client, workspace_ctx):
    from sqlalchemy import func, select

    from app.core.db import get_session_factory
    from app.models.knowledge import Chunk

    source = await create_source(
        client,
        workspace_ctx,
        type="sitemap",
        config={"sitemap_url": "https://site.example.com/sitemap.xml"},
    )
    with respx.mock:
        respx.get("https://site.example.com/sitemap.xml").mock(
            return_value=sitemap_response(
                ["https://site.example.com/a", "https://site.example.com/b"]
            )
        )
        respx.get("https://site.example.com/a").mock(return_value=html_page("Alpha", "A stays."))
        respx.get("https://site.example.com/b").mock(return_value=html_page("Beta", "B goes."))
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    removed = next(d for d in docs if d["uri"] == "https://site.example.com/b")

    with respx.mock:  # second sync: /b vanished from the sitemap
        respx.get("https://site.example.com/sitemap.xml").mock(
            return_value=sitemap_response(["https://site.example.com/a"])
        )
        respx.get("https://site.example.com/a").mock(return_value=html_page("Alpha", "A stays."))
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["uri"] for d in docs] == ["https://site.example.com/a"]
    async with get_session_factory()() as session:
        chunk_count = (
            await session.execute(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == removed["id"])
            )
        ).scalar_one()
    assert chunk_count == 0


async def test_sitemap_resync_with_fetch_failure_keeps_the_doc_and_stays_idle(
    client, workspace_ctx
):
    source = await create_source(
        client,
        workspace_ctx,
        type="sitemap",
        config={"sitemap_url": "https://site.example.com/sitemap.xml"},
    )
    listing = ["https://site.example.com/a", "https://site.example.com/b"]
    with respx.mock:
        respx.get("https://site.example.com/sitemap.xml").mock(
            return_value=sitemap_response(listing)
        )
        respx.get("https://site.example.com/a").mock(return_value=html_page("Alpha", "A ok."))
        respx.get("https://site.example.com/b").mock(return_value=html_page("Beta", "B ok."))
        await sync_now(client, workspace_ctx, source["id"])

    with respx.mock:  # /b breaks on the re-sync — still listed, so no pruning
        respx.get("https://site.example.com/sitemap.xml").mock(
            return_value=sitemap_response(listing)
        )
        respx.get("https://site.example.com/a").mock(return_value=html_page("Alpha", "A ok."))
        respx.get("https://site.example.com/b").mock(return_value=httpx.Response(404))
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert len(docs) == 2  # nothing pruned on partial failure
    by_uri = {d["uri"]: d for d in docs}
    assert by_uri["https://site.example.com/b"]["status"] == "failed"
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    # Partial success: one page fetched fine, so the source stays usable ("idle")
    # with the per-page failure recorded in the summary.
    assert refreshed["status"] == "idle"
    assert "404" in refreshed["error"]


async def test_sitemap_sync_with_every_page_failing_goes_error_and_never_prunes(
    client, workspace_ctx
):
    source = await create_source(
        client,
        workspace_ctx,
        type="sitemap",
        config={"sitemap_url": "https://site.example.com/sitemap.xml"},
    )
    listing = ["https://site.example.com/a", "https://site.example.com/b"]
    with respx.mock:
        respx.get("https://site.example.com/sitemap.xml").mock(
            return_value=sitemap_response(listing)
        )
        respx.get("https://site.example.com/a").mock(return_value=html_page("Alpha", "A ok."))
        respx.get("https://site.example.com/b").mock(return_value=html_page("Beta", "B ok."))
        await sync_now(client, workspace_ctx, source["id"])

    with respx.mock:  # total outage — nothing fetched at all
        respx.get("https://site.example.com/sitemap.xml").mock(
            return_value=sitemap_response(listing)
        )
        respx.get("https://site.example.com/a").mock(return_value=httpx.Response(404))
        respx.get("https://site.example.com/b").mock(return_value=httpx.Response(404))
        await sync_now(client, workspace_ctx, source["id"])

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "error"  # nothing fetched → the sync failed
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert len(docs) == 2  # and a failed sync never prunes


# ---------------------------------------------------------------------------
# crawl
# ---------------------------------------------------------------------------


async def test_crawl_ingests_same_site_pages_within_base_path(client, workspace_ctx):
    base = "https://docs.example.com/docs/"
    source = await create_source(client, workspace_ctx, type="crawl", config={"base_url": base})
    with respx.mock:
        respx.get(base).mock(
            return_value=html_page(
                "Docs Home",
                "Welcome to the docs.",
                links=(
                    "a",
                    "b",
                    "https://other.example.com/offsite",  # off-site — excluded
                    "a#section",  # fragment dupe of a
                    "/pricing",  # outside the base path — excluded
                    "mailto:hi@example.com",
                ),
            )
        )
        respx.get("https://docs.example.com/docs/a").mock(
            return_value=html_page("Alpha", "Alpha body.")
        )
        respx.get("https://docs.example.com/docs/b").mock(
            return_value=html_page("Beta", "Beta body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["uri"] for d in docs} == {
        base,
        "https://docs.example.com/docs/a",
        "https://docs.example.com/docs/b",
    }
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_crawl_respects_max_depth(client, workspace_ctx):
    base = "https://docs.example.com/guide/"
    source = await create_source(
        client, workspace_ctx, type="crawl", config={"base_url": base, "max_depth": 1}
    )
    with respx.mock:
        respx.get(base).mock(return_value=html_page("Root", "Root text.", links=("level1",)))
        respx.get("https://docs.example.com/guide/level1").mock(
            return_value=html_page("Level 1", "One deep.", links=("level2",))
        )
        # level2 is never mocked: fetching it would fail the sync
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"Root", "Level 1"}
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_crawl_suppresses_duplicate_content_pages(client, workspace_ctx):
    base = "https://docs.example.com/kb/"
    source = await create_source(client, workspace_ctx, type="crawl", config={"base_url": base})
    with respx.mock:
        respx.get(base).mock(return_value=html_page("KB Home", "Home text.", links=("a", "b")))
        respx.get("https://docs.example.com/kb/a").mock(
            return_value=html_page("Same Page", "Identical body.")
        )
        respx.get("https://docs.example.com/kb/b").mock(
            return_value=html_page("Same Page", "Identical body.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert len(docs) == 2  # home + one copy of the duplicated page
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


# ---------------------------------------------------------------------------
# github
# ---------------------------------------------------------------------------


async def test_github_files_filtered_fetched_and_indexed(client, workspace_ctx):
    source = await create_source(
        client, workspace_ctx, type="github", config={"repo_owner": "acme", "repo": "docs"}
    )
    license_b64 = base64.b64encode(b"MIT License full body text.").decode()
    with respx.mock:
        respx.get("https://api.github.com/repos/acme/docs").mock(
            return_value=httpx.Response(200, json={"default_branch": "main"})
        )
        respx.get("https://api.github.com/repos/acme/docs/git/trees/main").mock(
            return_value=httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "README.md", "type": "blob", "size": 100},
                        {"path": "docs/guide.md", "type": "blob", "size": 200},
                        {"path": "LICENSE", "type": "blob", "size": 300},
                        {"path": "src/app.py", "type": "blob", "size": 50},  # extension
                        {"path": "node_modules/pkg/readme.md", "type": "blob", "size": 10},
                        {"path": "huge.md", "type": "blob", "size": 2 * 1024 * 1024},  # >1MB
                        {"path": "docs", "type": "tree"},  # not a blob
                    ]
                },
            )
        )
        respx.get("https://api.github.com/repos/acme/docs/contents/README.md").mock(
            return_value=httpx.Response(
                200,
                text="# Readme\n\nWelcome to the project.",
                headers={"content-type": "text/plain; charset=utf-8"},
            )
        )
        respx.get("https://api.github.com/repos/acme/docs/contents/docs/guide.md").mock(
            return_value=httpx.Response(
                200,
                text="# Guide\n\nHow to use it.",
                headers={"content-type": "text/plain; charset=utf-8"},
            )
        )
        respx.get("https://api.github.com/repos/acme/docs/contents/LICENSE").mock(
            return_value=httpx.Response(  # raw unsupported → base64 JSON fallback
                200, json={"content": license_b64, "encoding": "base64"}
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    by_title = {d["title"]: d for d in docs}
    assert set(by_title) == {"README.md", "docs/guide.md", "LICENSE"}
    assert all(d["status"] == "indexed" for d in docs)
    assert by_title["README.md"]["uri"] == "https://github.com/acme/docs/blob/main/README.md"
    assert by_title["README.md"]["mime"] == "text/markdown"
    assert by_title["LICENSE"]["mime"] == "text/plain"
    assert "MIT License" in await doc_text(client, workspace_ctx, by_title["LICENSE"]["id"])


async def test_github_issues_with_comments_skips_pull_requests(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="github",
        config={
            "repo_owner": "acme",
            "repo": "docs",
            "include_files": False,
            "include_issues": True,
        },
    )
    with respx.mock:
        respx.get("https://api.github.com/repos/acme/docs/issues").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "number": 7,
                        "title": "Widget broken",
                        "body": "It crashes on load.",
                        "comments": 2,
                        "html_url": "https://github.com/acme/docs/issues/7",
                    },
                    {
                        "number": 8,
                        "title": "A pull request",
                        "body": "PRs interleave in the issues feed",
                        "comments": 0,
                        "html_url": "https://github.com/acme/docs/pull/8",
                        "pull_request": {"url": "https://api.github.com/..."},
                    },
                ],
            )
        )
        respx.get("https://api.github.com/repos/acme/docs/issues/7/comments").mock(
            return_value=httpx.Response(
                200, json=[{"body": "Same here."}, {"body": "Fixed in v1.2."}]
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["title"] for d in docs] == ["#7 Widget broken"]
    assert docs[0]["uri"] == "https://github.com/acme/docs/issues/7"
    assert "Fixed in v1.2." in await doc_text(client, workspace_ctx, docs[0]["id"])


async def test_github_prs_title_and_body_only(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="github",
        config={"repo_owner": "acme", "repo": "docs", "include_files": False, "include_prs": True},
    )
    with respx.mock:
        respx.get("https://api.github.com/repos/acme/docs/pulls").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "number": 3,
                        "title": "Add docs",
                        "body": "This PR adds the docs site.",
                        "html_url": "https://github.com/acme/docs/pull/3",
                    }
                ],
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["title"] for d in docs] == ["#3 Add docs"]
    assert docs[0]["uri"] == "https://github.com/acme/docs/pull/3"


async def test_github_bad_credentials_sets_error_with_api_message(client, workspace_ctx):
    source = await create_source(
        client, workspace_ctx, type="github", config={"repo_owner": "acme", "repo": "private"}
    )
    with respx.mock:
        respx.get("https://api.github.com/repos/acme/private").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        await sync_now(client, workspace_ctx, source["id"])

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "error"
    assert "401" in refreshed["error"]
    assert "Bad credentials" in refreshed["error"]


async def test_github_token_sent_as_bearer_header(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="github",
        config={
            "repo_owner": "acme",
            "repo": "docs",
            "include_files": False,
            "include_issues": True,
        },
        secrets={"token": "ghp_secret_123"},
    )
    assert source["has_secrets"] is True
    with respx.mock:
        route = respx.get("https://api.github.com/repos/acme/docs/issues").mock(
            return_value=httpx.Response(200, json=[])
        )
        await sync_now(client, workspace_ctx, source["id"])
    assert route.called
    assert route.calls.last.request.headers["authorization"] == "Bearer ghp_secret_123"
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


# ---------------------------------------------------------------------------
# notion
# ---------------------------------------------------------------------------


def notion_page(page_id, title):
    return {
        "object": "page",
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}},
    }


def notion_blocks(results, has_more=False, next_cursor=None):
    return httpx.Response(
        200, json={"results": results, "has_more": has_more, "next_cursor": next_cursor}
    )


def notion_block(block_id, block_type, text, **payload):
    return {
        "object": "block",
        "id": block_id,
        "type": block_type,
        "has_children": payload.pop("has_children", False),
        block_type: {"rich_text": [{"plain_text": text}], **payload},
    }


async def test_notion_source_requires_token_on_create(client, workspace_ctx):
    for secrets in (None, {"token": ""}):
        body = {"type": "notion", "name": "Wiki", "config": {}}
        if secrets is not None:
            body["secrets"] = secrets
        denied = await client.post(
            f"{workspace_ctx.base}/knowledge/sources",
            json=body,
            headers=workspace_ctx.owner_headers,
        )
        assert denied.status_code == 422, denied.text
    ok = await create_source(client, workspace_ctx, type="notion", secrets={"token": "ntn_secret"})
    assert ok["has_secrets"] is True


async def test_notion_search_renders_blocks_and_follows_cursor(client, workspace_ctx):
    source = await create_source(
        client, workspace_ctx, type="notion", secrets={"token": "ntn_secret"}
    )
    with respx.mock:
        search_route = respx.post("https://api.notion.com/v1/search").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "results": [notion_page("p1", "Guide")],
                        "has_more": True,
                        "next_cursor": "cur2",
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "results": [notion_page("p2", "Runbook")],
                        "has_more": False,
                        "next_cursor": None,
                    },
                ),
            ]
        )
        respx.get("https://api.notion.com/v1/blocks/p1/children").mock(
            return_value=notion_blocks(
                [
                    notion_block("b1", "heading_1", "Setup"),
                    notion_block("b2", "paragraph", "Install the app."),
                    notion_block("b3", "bulleted_list_item", "step one"),
                    notion_block("b4", "to_do", "enable sso", checked=True),
                    notion_block("b5", "toggle", "Advanced", has_children=True),
                    notion_block("b6", "unsupported_widget", "ignored"),
                ]
            )
        )
        respx.get("https://api.notion.com/v1/blocks/b5/children").mock(
            return_value=notion_blocks([notion_block("b7", "paragraph", "Hidden details.")])
        )
        respx.get("https://api.notion.com/v1/blocks/p2/children").mock(
            return_value=notion_blocks([notion_block("b8", "paragraph", "On call rota.")])
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert search_route.call_count == 2
    second_body = json.loads(search_route.calls[1].request.content)
    assert second_body["start_cursor"] == "cur2"

    docs = await list_docs(client, workspace_ctx, source["id"])
    by_title = {d["title"]: d for d in docs}
    assert set(by_title) == {"Guide", "Runbook"}
    assert by_title["Guide"]["uri"] == "https://notion.so/p1"
    guide_text = await doc_text(client, workspace_ctx, by_title["Guide"]["id"])
    assert "# Setup" in guide_text
    assert "- step one" in guide_text
    assert "- [x] enable sso" in guide_text
    assert "Hidden details." in guide_text  # nested has_children rendered
    assert "ignored" not in guide_text  # unknown block types skipped


async def test_notion_root_page_traversal_discovers_child_pages(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="notion",
        config={"root_page_id": "r1"},
        secrets={"token": "ntn_secret"},
    )
    child_page_block = {
        "object": "block",
        "id": "c1",
        "type": "child_page",
        "has_children": True,
        "child_page": {"title": "Child"},
    }
    with respx.mock:
        respx.get("https://api.notion.com/v1/pages/r1").mock(
            return_value=httpx.Response(200, json=notion_page("r1", "Root"))
        )
        respx.get("https://api.notion.com/v1/blocks/r1/children").mock(
            return_value=notion_blocks(
                [notion_block("b1", "paragraph", "Welcome."), child_page_block]
            )
        )
        respx.get("https://api.notion.com/v1/pages/c1").mock(
            return_value=httpx.Response(200, json=notion_page("c1", "Child"))
        )
        respx.get("https://api.notion.com/v1/blocks/c1/children").mock(
            return_value=notion_blocks([notion_block("b2", "paragraph", "Child body.")])
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"Root", "Child"}
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_notion_unauthorized_mentions_integration_token(client, workspace_ctx):
    source = await create_source(
        client, workspace_ctx, type="notion", secrets={"token": "ntn_revoked"}
    )
    with respx.mock:
        respx.post("https://api.notion.com/v1/search").mock(
            return_value=httpx.Response(401, json={"message": "API token is invalid."})
        )
        await sync_now(client, workspace_ctx, source["id"])

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "error"
    assert "integration token" in refreshed["error"]


# ---------------------------------------------------------------------------
# secrets, validation, authz, search passthrough
# ---------------------------------------------------------------------------


async def test_secrets_stored_encrypted_and_never_returned(client, workspace_ctx):
    from app.core.db import get_session_factory
    from app.models.knowledge import KnowledgeSource
    from app.services.knowledge import get_source_secrets

    source = await create_source(
        client,
        workspace_ctx,
        type="github",
        config={"repo_owner": "acme", "repo": "docs"},
        secrets={"token": "ghp_plain_secret"},
    )
    assert source["has_secrets"] is True
    assert "secrets" not in source
    assert "ghp_plain_secret" not in json.dumps(source)

    async with get_session_factory()() as session:
        row = await session.get(KnowledgeSource, source["id"])
        assert row.secrets_encrypted
        assert "ghp_plain_secret" not in row.secrets_encrypted  # encrypted at rest
        assert get_source_secrets(row) == {"token": "ghp_plain_secret"}

    listed = await client.get(
        f"{workspace_ctx.base}/knowledge/sources", headers=workspace_ctx.owner_headers
    )
    assert "ghp_plain_secret" not in listed.text
    detail = await get_source_json(client, workspace_ctx, source["id"])
    assert detail["has_secrets"] is True
    assert "ghp_plain_secret" not in json.dumps(detail)

    cleared = await client.patch(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}",
        json={"secrets": {}},
        headers=workspace_ctx.owner_headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_secrets"] is False


async def test_connector_config_validation(client, workspace_ctx):
    bad_bodies = [
        {"type": "sitemap", "name": "S", "config": {}},  # missing sitemap_url
        {"type": "sitemap", "name": "S", "config": {"sitemap_url": "ftp://x"}},
        {"type": "crawl", "name": "C", "config": {}},  # missing base_url
        {
            "type": "crawl",
            "name": "C",
            "config": {"base_url": "https://x.example.com", "max_depth": "three"},
        },
        {"type": "github", "name": "G", "config": {"repo_owner": "acme"}},  # missing repo
        {
            "type": "sitemap",
            "name": "S",
            "config": {"sitemap_url": "https://x.example.com/s.xml", "refresh_minutes": 2},
        },  # refresh below the 5 minute floor
    ]
    for body in bad_bodies:
        response = await client.post(
            f"{workspace_ctx.base}/knowledge/sources",
            json=body,
            headers=workspace_ctx.owner_headers,
        )
        assert response.status_code == 422, (body, response.text)

    clamped = await create_source(
        client,
        workspace_ctx,
        type="crawl",
        config={"base_url": "https://x.example.com/docs", "max_pages": 9999},
    )
    assert clamped["config"]["max_pages"] == 200  # capped


async def test_viewer_cannot_create_connector_sources(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("viewer@example.com", role="viewer")
    denied = await client.post(
        f"{workspace_ctx.base}/knowledge/sources",
        json={
            "type": "sitemap",
            "name": "Nope",
            "config": {"sitemap_url": "https://x.example.com/s.xml"},
        },
        headers=viewer,
    )
    assert denied.status_code == 403


async def test_connector_sources_are_workspace_isolated(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="sitemap",
        config={"sitemap_url": "https://site.example.com/sitemap.xml"},
    )
    other_auth = await signup(client, "spy@example.com")
    other_ws = (
        await client.post("/api/v1/workspaces", json={"name": "Spy Co"}, headers=bearer(other_auth))
    ).json()
    other_base = f"/api/v1/w/{other_ws['id']}"

    assert (
        await client.get(
            f"{other_base}/knowledge/sources/{source['id']}", headers=bearer(other_auth)
        )
    ).status_code == 404
    assert (
        await client.post(
            f"{other_base}/knowledge/sources/{source['id']}/sync", headers=bearer(other_auth)
        )
    ).status_code == 404


async def test_search_accepts_rerank_flag(client, workspace_ctx):
    from tests.knowledge.conftest import paste_text

    source = await create_source(client, workspace_ctx, type="text", name="Notes")
    await paste_text(
        client,
        workspace_ctx,
        source["id"],
        title="Sky facts",
        content="The sky is blue during the day.",
    )
    await drain_tasks()
    response = await client.post(
        f"{workspace_ctx.base}/knowledge/search",
        json={"query": "sky", "rerank": True},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"]
