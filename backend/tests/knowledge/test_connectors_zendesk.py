"""Zendesk help-center connector: incremental cursor advance, archived pruning,
locale/draft filtering, pagination, Basic auth shape, 429/Retry-After caps,
config/secret validation. All HTTP respx-mocked."""

from __future__ import annotations

import base64

import httpx
import respx

from tests.knowledge.test_connectors import (
    create_source,
    doc_text,
    get_source_json,
    list_docs,
    sync_now,
)

BASE = "https://acme.zendesk.com"
INCREMENTAL = f"{BASE}/api/v2/help_center/incremental/articles"


def article(article_id, title, body, *, locale="en-us", draft=False, archived=False):
    return {
        "id": article_id,
        "title": title,
        "body": body,
        "locale": locale,
        "draft": draft,
        "archived": archived,
        "html_url": f"https://acme.zendesk.com/hc/{locale}/articles/{article_id}",
    }


def incremental_page(articles, *, end_time, next_page=None):
    return httpx.Response(
        200,
        json={
            "articles": articles,
            "end_time": end_time,
            "next_page": next_page,
            "count": len(articles),
        },
    )


async def make_zendesk_source(client, ctx, *, config=None):
    return await create_source(
        client,
        ctx,
        type="zendesk",
        config={"subdomain": "acme", **(config or {})},
        secrets={"email": "ops@acme.com", "api_token": "z-secret"},
    )


# ---------------------------------------------------------------------------
# incremental sync
# ---------------------------------------------------------------------------


async def test_zendesk_sync_ingests_articles_and_advances_cursor(client, workspace_ctx):
    source = await make_zendesk_source(client, workspace_ctx)
    with respx.mock:
        route = respx.get(INCREMENTAL).mock(
            return_value=incremental_page(
                [
                    article(101, "Reset password", "<h2>Steps</h2><p>Click reset, check mail.</p>"),
                    article(102, "Secret draft", "<p>Unpublished.</p>", draft=True),
                    article(103, "Article FR", "<p>En français.</p>", locale="fr"),
                ],
                end_time=1700,
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    request = route.calls.last.request
    assert request.url.params["start_time"] == "0"
    expected_auth = "Basic " + base64.b64encode(b"ops@acme.com/token:z-secret").decode()
    assert request.headers["authorization"] == expected_auth

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["title"] for d in docs] == ["Reset password"]  # draft + other locale skipped
    assert docs[0]["uri"] == "https://acme.zendesk.com/hc/en-us/articles/101"
    text = await doc_text(client, workspace_ctx, docs[0]["id"])
    assert "Click reset, check mail." in text  # HTML body → text
    assert "<p>" not in text

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "idle"
    assert refreshed["config"]["sync_cursor"] == 1700  # persisted watermark


async def test_zendesk_second_sync_resumes_cursor_and_prunes_archived(client, workspace_ctx):
    source = await make_zendesk_source(client, workspace_ctx)
    with respx.mock:
        respx.get(INCREMENTAL).mock(
            return_value=incremental_page(
                [
                    article(1, "Keep me", "<p>Evergreen content.</p>"),
                    article(2, "Retire me", "<p>Old content.</p>"),
                ],
                end_time=1700,
            )
        )
        await sync_now(client, workspace_ctx, source["id"])
    assert len(await list_docs(client, workspace_ctx, source["id"])) == 2

    with respx.mock:
        # Incremental listing only names what changed: article 2 got archived.
        # Article 1 is absent — it must NOT be pruned.
        route = respx.get(INCREMENTAL).mock(
            return_value=incremental_page(
                [article(2, "Retire me", "<p>Old content.</p>", archived=True)],
                end_time=1800,
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert route.calls.last.request.url.params["start_time"] == "1700"
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["title"] for d in docs] == ["Keep me"]  # archived pruned, unchanged kept
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "idle"
    assert refreshed["config"]["sync_cursor"] == 1800


async def test_zendesk_follows_same_host_next_page(client, workspace_ctx):
    source = await make_zendesk_source(client, workspace_ctx)
    with respx.mock:
        route = respx.get(INCREMENTAL).mock(
            side_effect=[
                incremental_page(
                    [article(1, "One", "<p>First.</p>")],
                    end_time=500,
                    next_page=f"{INCREMENTAL}?start_time=500",
                ),
                incremental_page([article(2, "Two", "<p>Second.</p>")], end_time=900),
            ]
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert route.call_count == 2
    assert route.calls[1].request.url.params["start_time"] == "500"
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"One", "Two"}
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["config"]["sync_cursor"] == 900


# ---------------------------------------------------------------------------
# rate limiting (the incremental API allows 10 req/min)
# ---------------------------------------------------------------------------


async def test_zendesk_429_retry_after_is_honored(client, workspace_ctx):
    source = await make_zendesk_source(client, workspace_ctx)
    with respx.mock:
        route = respx.get(INCREMENTAL).mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "0"}),
                incremental_page([article(1, "Finally", "<p>Made it.</p>")], end_time=42),
            ]
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert route.call_count == 2
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


def test_retry_after_sleep_is_capped():
    from app.rag.connectors import retry_after_seconds

    assert retry_after_seconds(httpx.Response(429, headers={"retry-after": "600"})) == 30.0
    assert retry_after_seconds(httpx.Response(429, headers={"retry-after": "2"})) == 2.0
    assert retry_after_seconds(httpx.Response(429)) == 1.0  # header missing
    assert retry_after_seconds(httpx.Response(429, headers={"retry-after": "soon"})) == 1.0


async def test_zendesk_persistent_429_marks_source_errored(client, workspace_ctx):
    source = await make_zendesk_source(client, workspace_ctx)
    with respx.mock:
        route = respx.get(INCREMENTAL).mock(
            return_value=httpx.Response(429, headers={"retry-after": "0"})
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert route.call_count == 3  # initial try + 2 retries, then give up
    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "error"
    assert "rate limited" in refreshed["error"]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


async def test_zendesk_config_and_secret_validation(client, workspace_ctx):
    good_secrets = {"email": "ops@acme.com", "api_token": "z-secret"}
    bad_bodies = [
        {"type": "zendesk", "name": "Z", "config": {}, "secrets": good_secrets},
        # a full host / path must not pass as a subdomain (SSRF guard)
        {
            "type": "zendesk",
            "name": "Z",
            "config": {"subdomain": "evil.com"},
            "secrets": good_secrets,
        },
        {"type": "zendesk", "name": "Z", "config": {"subdomain": "a/b"}, "secrets": good_secrets},
        # secrets are mandatory: both keys, non-empty
        {"type": "zendesk", "name": "Z", "config": {"subdomain": "acme"}},
        {
            "type": "zendesk",
            "name": "Z",
            "config": {"subdomain": "acme"},
            "secrets": {"email": "ops@acme.com"},
        },
        {
            "type": "zendesk",
            "name": "Z",
            "config": {"subdomain": "acme"},
            "secrets": {"email": "", "api_token": "z"},
        },
        # the internal cursor must survive config round-trips but only as an int
        {
            "type": "zendesk",
            "name": "Z",
            "config": {"subdomain": "acme", "sync_cursor": "abc"},
            "secrets": good_secrets,
        },
    ]
    for body in bad_bodies:
        response = await client.post(
            f"{workspace_ctx.base}/knowledge/sources",
            json=body,
            headers=workspace_ctx.owner_headers,
        )
        assert response.status_code == 422, (body, response.text)

    created = await create_source(
        client,
        workspace_ctx,
        type="zendesk",
        config={"subdomain": "ACME", "locale": "DE"},  # normalized to lowercase
        secrets=good_secrets,
    )
    assert created["config"]["subdomain"] == "acme"
    assert created["config"]["locale"] == "de"
    assert created["has_secrets"] is True


async def test_zendesk_custom_locale_filters_articles(client, workspace_ctx):
    source = await make_zendesk_source(client, workspace_ctx, config={"locale": "de"})
    with respx.mock:
        respx.get(INCREMENTAL).mock(
            return_value=incremental_page(
                [
                    article(7, "Deutsch", "<p>Hallo Welt.</p>", locale="de"),
                    article(8, "English", "<p>Hello world.</p>", locale="en-us"),
                ],
                end_time=99,
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["title"] for d in docs] == ["Deutsch"]
