"""Auth on the workspace /mcp surface: key resolution, scopes, agent binding."""

from __future__ import annotations

from tests.mcp.conftest import (
    call_tool,
    list_tool_names,
    make_agent_row,
    make_api_key,
    rpc,
    seed_document,
)

AUTH_ERROR = "Authentication required. Provide a valid API key."


async def test_initialize_and_tools_list(client, workspace_ctx):
    body = await rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    )
    assert body["result"]["serverInfo"]["name"] == "Stept"

    names = await list_tool_names(client)
    assert {
        "search_knowledge",
        "ask_knowledge_base",
        "search_articles",
        "get_article",
        "get_document",
        "list_tours",
        "get_tour_steps",
        "tours_health",
        "search_conversations",
        "get_conversation",
        "add_conversation_note",
        "create_document",
    } <= names


async def test_call_without_key_returns_auth_error(client, workspace_ctx):
    payload = await call_tool(client, "search_knowledge", {"query": "refunds"})
    assert payload == [{"error": AUTH_ERROR}]
    # Dict-shaped tools return the bare error object.
    payload = await call_tool(client, "ask_knowledge_base", {"question": "hi"})
    assert payload == {"error": AUTH_ERROR}


async def test_call_with_garbage_key_returns_auth_error(client, workspace_ctx):
    payload = await call_tool(
        client, "search_knowledge", {"query": "refunds"}, key="sk_stept_not_a_real_key"
    )
    assert payload == [{"error": AUTH_ERROR}]


async def test_revoked_key_refused(client, workspace_ctx):
    created = await make_api_key(client, workspace_ctx)
    revoke = await client.delete(
        f"{workspace_ctx.base}/api-keys/{created['id']}", headers=workspace_ctx.owner_headers
    )
    assert revoke.status_code == 200
    payload = await call_tool(client, "search_knowledge", {"query": "x"}, key=created["key"])
    assert payload == [{"error": AUTH_ERROR}]


async def test_agent_bound_key_refused_on_workspace_mcp(client, workspace_ctx):
    agent_id = await make_agent_row(workspace_ctx.id)
    created = await make_api_key(client, workspace_ctx, scopes=["read"], agent_id=agent_id)
    payload = await call_tool(client, "search_knowledge", {"query": "x"}, key=created["key"])
    assert payload == [{"error": AUTH_ERROR}]


async def test_read_scope_key_cannot_call_write_tool(client, workspace_ctx):
    created = await make_api_key(client, workspace_ctx, scopes=["read"])
    payload = await call_tool(
        client,
        "create_document",
        {"title": "T", "content_markdown": "body"},
        key=created["key"],
    )
    assert payload == {
        "error": (
            "This API key lacks the knowledge:write permission — "
            "mint one with the write scope in Settings → MCP."
        )
    }


async def test_env_key_fallback_for_stdio(client, workspace_ctx, monkeypatch):
    """Under the stdio bridge (no headers exist) the key comes from the env."""
    from app.mcp import auth as mcp_auth

    created = await make_api_key(client, workspace_ctx)
    monkeypatch.setenv("STEPT_API_KEY", created["key"])
    monkeypatch.setattr(mcp_auth, "_stdio_mode", True)
    payload = await call_tool(client, "search_knowledge", {"query": "anything"})
    assert payload == []  # authenticated; nothing indexed yet


async def test_env_key_does_not_authenticate_http_requests(client, workspace_ctx, monkeypatch):
    """An operator exporting STEPT_API_KEY for the API process must not turn
    /mcp into an unauthenticated, fully-scoped endpoint."""
    created = await make_api_key(client, workspace_ctx)
    monkeypatch.setenv("STEPT_API_KEY", created["key"])
    # _stdio_mode stays False: this process serves HTTP, not the stdio bridge.
    payload = await call_tool(client, "search_knowledge", {"query": "anything"})
    assert "Authentication required" in str(payload)


async def test_key_is_scoped_to_its_own_workspace(client, workspace_ctx):
    from tests.conftest import bearer, signup

    await seed_document(client, workspace_ctx, title="Acme secret", content="Acme refunds doc")

    other_auth = await signup(client, "other-owner@example.com")
    other_ws = (
        await client.post(
            "/api/v1/workspaces", json={"name": "Other Co"}, headers=bearer(other_auth)
        )
    ).json()
    other_key = await client.post(
        f"/api/v1/w/{other_ws['id']}/api-keys",
        json={"name": "other", "scopes": ["read"]},
        headers=bearer(other_auth),
    )
    payload = await call_tool(
        client, "search_knowledge", {"query": "Acme refunds"}, key=other_key.json()["key"]
    )
    assert payload == []  # the other workspace has no documents
