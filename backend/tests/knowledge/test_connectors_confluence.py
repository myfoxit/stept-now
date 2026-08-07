"""Confluence connector: token + OAuth auth paths, space filter, storage→text,
pagination, 429 handling, config/secret validation. All HTTP respx-mocked; the
integrations token seam is monkeypatched per its documented contract."""

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

TOKEN_BASE = "https://acme.example.com"
CLOUD_API = "https://api.atlassian.com/ex/confluence"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class SeamConnection:
    """The attribute surface the connectors read off an IntegrationConnection."""

    def __init__(self, *, provider, connection_id="conn-1", meta=None):
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


def space(space_id, key, name=None):
    return {"id": space_id, "key": key, "name": name or key}


def confluence_page(page_id, title, html, *, space_key="DOC"):
    return {
        "id": page_id,
        "title": title,
        "body": {"storage": {"value": html, "representation": "storage"}},
        "_links": {"webui": f"/spaces/{space_key}/pages/{page_id}/{title.replace(' ', '+')}"},
    }


def listing(results, next_link=None):
    links = {"next": next_link} if next_link else {}
    return httpx.Response(200, json={"results": results, "_links": links})


# ---------------------------------------------------------------------------
# token mode
# ---------------------------------------------------------------------------


async def test_confluence_token_mode_syncs_storage_pages(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="confluence",
        config={"auth": "token", "base_url": TOKEN_BASE, "email": "ops@acme.com"},
        secrets={"api_token": "atl-secret"},
    )
    assert source["has_secrets"] is True
    with respx.mock:
        spaces_route = respx.get(f"{TOKEN_BASE}/wiki/api/v2/spaces").mock(
            return_value=listing([space("s1", "DOC")])
        )
        respx.get(f"{TOKEN_BASE}/wiki/api/v2/spaces/s1/pages").mock(
            return_value=listing(
                [
                    confluence_page(
                        "p1", "Install Guide", "<h2>Install</h2><p>Run the installer fast.</p>"
                    ),
                    confluence_page("p2", "FAQ", "<p>Answers to everything.</p>"),
                ]
            )
        )
        await sync_now(client, workspace_ctx, source["id"])

    expected_auth = "Basic " + base64.b64encode(b"ops@acme.com:atl-secret").decode()
    assert spaces_route.calls.last.request.headers["authorization"] == expected_auth
    assert spaces_route.calls.last.request.url.params["type"] == "global"

    docs = await list_docs(client, workspace_ctx, source["id"])
    by_title = {d["title"]: d for d in docs}
    assert set(by_title) == {"Install Guide", "FAQ"}
    assert (
        by_title["Install Guide"]["uri"] == f"{TOKEN_BASE}/wiki/spaces/DOC/pages/p1/Install+Guide"
    )
    assert all(d["status"] == "indexed" for d in docs)
    text = await doc_text(client, workspace_ctx, by_title["Install Guide"]["id"])
    assert "Install" in text
    assert "Run the installer fast." in text  # storage XHTML → text conversion
    assert "<p>" not in text
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_confluence_space_keys_filter_case_insensitive(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="confluence",
        config={
            "auth": "token",
            "base_url": TOKEN_BASE,
            "email": "ops@acme.com",
            "space_keys": ["doc"],  # lowercase on purpose; keys are usually upper
        },
        secrets={"api_token": "atl-secret"},
    )
    with respx.mock:
        respx.get(f"{TOKEN_BASE}/wiki/api/v2/spaces").mock(
            return_value=listing([space("s1", "DOC"), space("s2", "ENG")])
        )
        # ENG's pages endpoint is never mocked — fetching it would blow the sync.
        respx.get(f"{TOKEN_BASE}/wiki/api/v2/spaces/s1/pages").mock(
            return_value=listing([confluence_page("p1", "Only Doc", "<p>Doc space only.</p>")])
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert [d["title"] for d in docs] == ["Only Doc"]
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_confluence_follows_next_cursor_links(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="confluence",
        config={"auth": "token", "base_url": TOKEN_BASE, "email": "ops@acme.com"},
        secrets={"api_token": "atl-secret"},
    )
    with respx.mock:
        respx.get(f"{TOKEN_BASE}/wiki/api/v2/spaces").mock(
            return_value=listing([space("s1", "DOC")])
        )
        pages_route = respx.get(f"{TOKEN_BASE}/wiki/api/v2/spaces/s1/pages").mock(
            side_effect=[
                listing(
                    [confluence_page("p1", "One", "<p>First page.</p>")],
                    next_link="/wiki/api/v2/spaces/s1/pages?cursor=c2",
                ),
                listing([confluence_page("p2", "Two", "<p>Second page.</p>")]),
            ]
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert pages_route.call_count == 2
    assert pages_route.calls[1].request.url.params["cursor"] == "c2"
    docs = await list_docs(client, workspace_ctx, source["id"])
    assert {d["title"] for d in docs} == {"One", "Two"}


async def test_confluence_unauthorized_mentions_credentials(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="confluence",
        config={"auth": "token", "base_url": TOKEN_BASE, "email": "ops@acme.com"},
        secrets={"api_token": "atl-revoked"},
    )
    with respx.mock:
        respx.get(f"{TOKEN_BASE}/wiki/api/v2/spaces").mock(return_value=httpx.Response(401))
        await sync_now(client, workspace_ctx, source["id"])

    refreshed = await get_source_json(client, workspace_ctx, source["id"])
    assert refreshed["status"] == "error"
    assert "401" in refreshed["error"]
    assert "credentials" in refreshed["error"]


async def test_confluence_429_waits_and_retries(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="confluence",
        config={"auth": "token", "base_url": TOKEN_BASE, "email": "ops@acme.com"},
        secrets={"api_token": "atl-secret"},
    )
    with respx.mock:
        spaces_route = respx.get(f"{TOKEN_BASE}/wiki/api/v2/spaces").mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "0"}),
                listing([space("s1", "DOC")]),
            ]
        )
        respx.get(f"{TOKEN_BASE}/wiki/api/v2/spaces/s1/pages").mock(
            return_value=listing([confluence_page("p1", "Patient", "<p>Worth the wait.</p>")])
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert spaces_route.call_count == 2  # 429 then success
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


# ---------------------------------------------------------------------------
# oauth mode (token seam)
# ---------------------------------------------------------------------------


async def test_confluence_oauth_uses_seam_token_and_connection_cloud_id(
    client, workspace_ctx, monkeypatch
):
    connection = SeamConnection(
        provider="confluence",
        meta={"cloud_id": "cid-9", "site_url": "https://acme.atlassian.net"},
    )
    calls = patch_tokens_seam(monkeypatch, connection, token="atl-bearer-token")
    source = await create_source(
        client,
        workspace_ctx,
        type="confluence",
        config={"auth": "oauth", "connection_id": "conn-1"},
    )
    assert source["has_secrets"] is False  # OAuth mode needs no pasted secrets
    with respx.mock:
        spaces_route = respx.get(f"{CLOUD_API}/cid-9/wiki/api/v2/spaces").mock(
            return_value=listing([space("s1", "DOC")])
        )
        respx.get(f"{CLOUD_API}/cid-9/wiki/api/v2/spaces/s1/pages").mock(
            return_value=listing([confluence_page("p1", "Cloud Page", "<p>Via 3LO.</p>")])
        )
        await sync_now(client, workspace_ctx, source["id"])

    assert calls == [(workspace_ctx.id, "conn-1")]
    assert spaces_route.calls.last.request.headers["authorization"] == "Bearer atl-bearer-token"
    docs = await list_docs(client, workspace_ctx, source["id"])
    # uri is the human site link, never the api.atlassian.com endpoint
    assert docs[0]["uri"] == "https://acme.atlassian.net/wiki/spaces/DOC/pages/p1/Cloud+Page"
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


async def test_confluence_oauth_multi_site_picks_config_cloud_id(
    client, workspace_ctx, monkeypatch
):
    connection = SeamConnection(
        provider="confluence",
        meta={
            "sites": [
                {"id": "cid-a", "url": "https://alpha.atlassian.net", "name": "Alpha"},
                {"id": "cid-b", "url": "https://beta.atlassian.net", "name": "Beta"},
            ]
        },
    )
    patch_tokens_seam(monkeypatch, connection)
    source = await create_source(
        client,
        workspace_ctx,
        type="confluence",
        config={"auth": "oauth", "connection_id": "conn-1", "cloud_id": "cid-b"},
    )
    with respx.mock:
        respx.get(f"{CLOUD_API}/cid-b/wiki/api/v2/spaces").mock(
            return_value=listing([space("s1", "DOC")])
        )
        respx.get(f"{CLOUD_API}/cid-b/wiki/api/v2/spaces/s1/pages").mock(
            return_value=listing([confluence_page("p1", "Beta Page", "<p>Second site.</p>")])
        )
        await sync_now(client, workspace_ctx, source["id"])

    docs = await list_docs(client, workspace_ctx, source["id"])
    assert docs[0]["uri"].startswith("https://beta.atlassian.net/wiki/")
    assert (await get_source_json(client, workspace_ctx, source["id"]))["status"] == "idle"


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


async def test_confluence_config_and_secret_validation(client, workspace_ctx):
    bad_bodies = [
        # token mode without base_url / email
        {"type": "confluence", "name": "C", "config": {"auth": "token"}},
        {
            "type": "confluence",
            "name": "C",
            "config": {"auth": "token", "base_url": TOKEN_BASE},
        },
        # token mode without the api_token secret
        {
            "type": "confluence",
            "name": "C",
            "config": {"auth": "token", "base_url": TOKEN_BASE, "email": "ops@acme.com"},
        },
        # oauth mode without a connection
        {"type": "confluence", "name": "C", "config": {"auth": "oauth"}},
        # nonsense auth mode
        {"type": "confluence", "name": "C", "config": {"auth": "magic"}},
        # space_keys must be a list of strings
        {
            "type": "confluence",
            "name": "C",
            "config": {
                "auth": "token",
                "base_url": TOKEN_BASE,
                "email": "ops@acme.com",
                "space_keys": "DOC",
            },
            "secrets": {"api_token": "t"},
        },
        # refresh below the 5 minute floor
        {
            "type": "confluence",
            "name": "C",
            "config": {"auth": "oauth", "connection_id": "conn-1", "refresh_minutes": 2},
        },
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
        type="confluence",
        config={
            "auth": "token",
            "base_url": f"{TOKEN_BASE}/wiki/",  # trailing /wiki is normalized away
            "email": "ops@acme.com",
            "max_pages": 9999,
        },
        secrets={"api_token": "atl-secret"},
    )
    assert clamped["config"]["max_pages"] == 500
    assert clamped["config"]["base_url"] == TOKEN_BASE
