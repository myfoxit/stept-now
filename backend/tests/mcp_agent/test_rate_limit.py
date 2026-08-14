"""Per-key rate limiting on the per-agent JSON-RPC endpoint (surface 2)."""

from __future__ import annotations

import pytest

from app.core.config import reset_settings_cache
from app.mcp.agent_endpoint import RATE_LIMITED
from app.mcp.auth import RATE_LIMITED_RPC_CODE
from tests.mcp_agent.conftest import make_mcp_agent, mint_key, rpc


def _set_limit(monkeypatch: pytest.MonkeyPatch, per_minute: int) -> None:
    monkeypatch.setenv("STEPT_MCP_RATE_LIMIT_PER_MINUTE", str(per_minute))
    reset_settings_cache()


async def test_over_limit_is_a_jsonrpc_error_with_the_request_id(
    client, workspace_ctx, monkeypatch
):
    _set_limit(monkeypatch, 2)
    agent_id = await make_mcp_agent(workspace_ctx.id)
    key = await mint_key(workspace_ctx.id)
    for i in range(2):
        response = await rpc(client, agent_id, key, "ping", msg_id=i + 1)
        assert response.status_code == 200, response.text
        assert response.json()["result"] == {}

    limited = await rpc(client, agent_id, key, "ping", msg_id=7)
    assert limited.status_code == 429
    body = limited.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 7  # the id is known here, so the error correlates
    assert body["error"]["code"] == RATE_LIMITED
    assert "rate limit" in body["error"]["message"].lower()
    # One code across both MCP surfaces.
    assert RATE_LIMITED == RATE_LIMITED_RPC_CODE


async def test_zero_disables_the_limiter(client, workspace_ctx, monkeypatch):
    _set_limit(monkeypatch, 0)
    agent_id = await make_mcp_agent(workspace_ctx.id)
    key = await mint_key(workspace_ctx.id)
    for i in range(6):
        assert (await rpc(client, agent_id, key, "ping", msg_id=i + 1)).status_code == 200
