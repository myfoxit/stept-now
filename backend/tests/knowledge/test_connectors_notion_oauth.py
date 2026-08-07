"""Notion OAuth mode: connection-backed sources need no pasted token, sync uses
the seam's bearer token, provider mismatches fail loudly. Token-mode behavior
is unchanged and stays covered by tests/knowledge/test_connectors.py."""

from __future__ import annotations

import httpx
import respx

from tests.knowledge.test_connectors import (
    create_source,
    get_source_json,
    list_docs,
    sync_now,
)

NOTION = "https://api.notion.com/v1"


class SeamConnection:
    """The attribute surface the connectors read off an IntegrationConnection."""

    def __init__(self, *, provider, connection_id="conn-n", meta=None):
        self.id = connection_id
        self.provider = provider
        self.status = "connected"
        self.meta = meta or {}


def patch_tokens_seam(monkeypatch, connection, token="seam-access-token"):
    """Fake BE-A's app.integrations.tokens seam (see docs/INTEGRATIONS-CONTRACTS.md)."""
    from app.integrations import tokens

    calls: list[tuple[str, str]] = []

    async def get_connection(session, workspace_id, connection_id):
        calls.append((workspace_id, connection_id))
        return connection

    async def get_valid_access_token(session, conn):
        return token

    monkeypatch.setattr(tokens, "get_connection", get_connection, raising=False)
    monkeypatch.setattr(tokens, "get_valid_access_token", get_valid_access_token, raising=False)
    return calls


def notion_page(page_id, title):
    return {
        "object": "page",
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}},
    }


def search_response(pages):
    return httpx.Response(200, json={"results": pages, "has_more": False, "next_cursor": None})


def blocks_response(texts):
    results = [
        {
            "object": "block",
            "id": f"b{i}",
            "type": "paragraph",
            "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": text}]},
        }
        for i, text in enumerate(texts)
    ]
    return httpx.Response(200, json={"results": results, "has_more": False, "next_cursor": None})


async def test_notion_oauth_source_needs_no_token_secret(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="notion",
        config={"auth": "oauth", "connection_id": "conn-n"},
    )
    assert source["has_secrets"] is False
    assert source["config"]["auth"] == "oauth"

    # …but oauth mode without a connection is rejected
    denied = await client.post(
        f"{workspace_ctx.base}/knowledge/sources",
        json={"type": "notion", "name": "Wiki", "config": {"auth": "oauth"}},
        headers=workspace_ctx.owner_headers,
    )
    assert denied.status_code == 422, denied.text

    # a bare connection_id implies oauth — also fine without secrets
    implied = await create_source(
        client, workspace_ctx, type="notion", config={"connection_id": "conn-n"}
    )
    assert implied["config"]["auth"] == "oauth"


async def test_notion_oauth_sync_uses_seam_bearer_token(client, workspace_ctx, monkeypatch):
    calls = patch_tokens_seam(
        monkeypatch, SeamConnection(provider="notion"), token="ntn-oauth-token"
    )
    source = await create_source(
        client,
        workspace_ctx,
        type="notion",
        config={"auth": "oauth", "connection_id": "conn-n"},
    )
    with respx.mock:
        search_route = respx.post(f"{NOTION}/search").mock(
            return_value=search_response([notion_page("p1", "Handbook")])
        )
        respx.get(f"{NOTION}/blocks/p1/children").mock(
            return_value=blocks_response(["All the answers live here."])
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert calls == [(workspace_ctx.id, "conn-n")]
    assert search_route.calls.last.request.headers["authorization"] == "Bearer ntn-oauth-token"

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["title"] for d in docs] == ["Handbook"]
    assert docs[0]["uri"] == "https://notion.so/p1"
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_notion_oauth_clearing_secrets_is_allowed(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="notion",
        config={"auth": "oauth", "connection_id": "conn-n"},
    )
    cleared = await client.patch(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}",
        json={"secrets": {}},
        headers=workspace_ctx.owner_headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["has_secrets"] is False


async def test_notion_oauth_provider_mismatch_fails_the_sync(client, workspace_ctx, monkeypatch):
    # The seam hands back a google connection — the notion connector must refuse.
    patch_tokens_seam(monkeypatch, SeamConnection(provider="google"))
    source = await create_source(
        client,
        workspace_ctx,
        type="notion",
        config={"auth": "oauth", "connection_id": "conn-n"},
    )
    with respx.mock:  # no Notion routes: no API call may happen
        await sync_now(client, workspace_ctx, source["id"])

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "error"
    assert "'google'" in refreshed["error"]
    assert "'notion'" in refreshed["error"]
