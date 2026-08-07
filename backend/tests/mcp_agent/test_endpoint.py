"""Auth matrix + JSON-RPC protocol behavior of POST /mcp/agents/{agent_id}."""

from __future__ import annotations

import pytest

from app.core import security
from app.core.db import session_scope, uuid7
from app.mcp import agent_endpoint
from app.models.api_key import ApiKey
from tests.conftest import bearer
from tests.mcp_agent.conftest import auth, make_mcp_agent, mint_key, rpc

#: Captured at import time, BEFORE the package's autouse fixture swaps in the
#: fake resolver — lets one smoke test exercise the real app.mcp.auth path.
_REAL_RESOLVE = agent_endpoint._resolve

# --- auth --------------------------------------------------------------------


async def test_missing_bearer_is_401_with_www_authenticate(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    response = await client.post(
        f"/mcp/agents/{agent_id}", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
    )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="stept-mcp"'
    assert "error" in response.json()


async def test_unknown_key_is_401(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    response = await rpc(client, agent_id, "raw-nope", "ping")
    assert response.status_code == 401
    assert "error" in response.json()


async def test_key_bound_to_a_different_agent_is_401(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    other_agent_id = await make_mcp_agent(workspace_ctx.id, name="Other")
    key = await mint_key(workspace_ctx.id, agent_id=other_agent_id)
    response = await rpc(client, agent_id, key, "ping")
    assert response.status_code == 401


async def test_agent_bound_key_works_on_its_own_agent(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    key = await mint_key(workspace_ctx.id, agent_id=agent_id)
    response = await rpc(client, agent_id, key, "ping")
    assert response.status_code == 200
    assert response.json()["result"] == {}


async def test_workspace_key_of_another_workspace_is_401(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Second"}, headers=bearer(workspace_ctx.owner_auth)
    )
    assert other.status_code == 201
    foreign_key = await mint_key(other.json()["id"])
    response = await rpc(client, agent_id, foreign_key, "ping")
    assert response.status_code == 401


async def test_mcp_disabled_agent_is_403(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id, enabled=False)
    key = await mint_key(workspace_ctx.id)
    response = await rpc(client, agent_id, key, "ping")
    assert response.status_code == 403
    assert response.json() == {"error": "MCP access is not enabled for this agent"}


# --- protocol ----------------------------------------------------------------


async def test_initialize_shape(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id, name="Finlike")
    key = await mint_key(workspace_ctx.id)
    response = await rpc(client, agent_id, key, "initialize", {"protocolVersion": "2025-06-18"})
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "stept-agent-mcp"
    assert result["serverInfo"]["agent"] == {"id": agent_id, "name": "Finlike"}


async def test_bad_json_is_parse_error_with_null_id(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    key = await mint_key(workspace_ctx.id)
    response = await client.post(
        f"/mcp/agents/{agent_id}",
        content=b"{nope",
        headers={**auth(key), "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] is None
    assert body["error"]["code"] == -32700


async def test_wrong_jsonrpc_version_is_invalid_request(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    key = await mint_key(workspace_ctx.id)
    response = await client.post(
        f"/mcp/agents/{agent_id}",
        json={"jsonrpc": "1.0", "id": 7, "method": "ping"},
        headers=auth(key),
    )
    body = response.json()
    assert body["error"]["code"] == -32600
    assert body["id"] == 7


async def test_unknown_method_is_method_not_found(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    key = await mint_key(workspace_ctx.id)
    response = await rpc(client, agent_id, key, "resources/list")
    assert response.json()["error"]["code"] == -32601


async def test_notifications_get_202_with_empty_body(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    key = await mint_key(workspace_ctx.id)

    # id: null → notification, even for a known method
    response = await rpc(client, agent_id, key, "tools/list", msg_id=None)
    assert response.status_code == 202
    assert response.content == b""

    # notifications/* → notification, even with an id
    response = await rpc(client, agent_id, key, "notifications/initialized", msg_id=3)
    assert response.status_code == 202
    assert response.content == b""


async def test_real_auth_module_end_to_end(client, workspace_ctx, monkeypatch):
    """No resolver mock: the seam calls the real ``app.mcp.auth`` (built by a
    parallel wave agent — skipped only if it has not landed yet)."""
    from app.mcp import auth as mcp_auth

    if not (hasattr(mcp_auth, "resolve_key_for_agent") or hasattr(mcp_auth, "resolve_key")):
        pytest.skip("app.mcp.auth not built yet (parallel wave agent)")
    monkeypatch.setattr(agent_endpoint, "_resolve", _REAL_RESOLVE)

    async def mint_real_key(agent_id: str | None = None) -> str:
        raw = f"{security.API_KEY_PREFIX}{uuid7()}"
        async with session_scope() as session:
            row = ApiKey(
                workspace_id=workspace_ctx.id,
                name="Real key",
                prefix=raw[:16],
                hashed_key=security.hash_api_key(raw),
                scopes=["read", "write"],
            )
            if hasattr(ApiKey, "agent_id"):
                row.agent_id = agent_id
            session.add(row)
            await session.commit()
        return raw

    agent_id = await make_mcp_agent(workspace_ctx.id)
    workspace_key = await mint_real_key()
    response = await rpc(client, agent_id, workspace_key, "ping")
    assert response.status_code == 200
    assert response.json()["result"] == {}

    if hasattr(ApiKey, "agent_id"):
        other_agent = await make_mcp_agent(workspace_ctx.id, name="Other real")
        bound_elsewhere = await mint_real_key(agent_id=other_agent)
        refused = await rpc(client, agent_id, bound_elsewhere, "ping")
        assert refused.status_code == 401
        bound_here = await mint_real_key(agent_id=agent_id)
        accepted = await rpc(client, agent_id, bound_here, "ping")
        assert accepted.status_code == 200


async def test_trailing_subpath_variant_is_accepted(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    key = await mint_key(workspace_ctx.id)
    response = await client.post(
        f"/mcp/agents/{agent_id}/",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers=auth(key),
    )
    assert response.status_code == 200
    assert response.json()["result"] == {}
