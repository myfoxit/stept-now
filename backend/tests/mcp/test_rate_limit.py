"""Per-key rate limiting on the workspace /mcp surface (enforced by the mount
shim, so every JSON-RPC method — initialize, tools/list, tools/call — counts).

The window reuses app.core.ratelimit's in-memory primitive; the autouse
``_fresh_state`` fixture resets it between tests.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import reset_settings_cache
from app.mcp.auth import RATE_LIMITED_RPC_CODE
from tests.mcp.conftest import MCP_HEADERS, make_api_key


def _set_limit(monkeypatch: pytest.MonkeyPatch, per_minute: int) -> None:
    monkeypatch.setenv("STEPT_MCP_RATE_LIMIT_PER_MINUTE", str(per_minute))
    reset_settings_cache()


async def _post_rpc(
    client: httpx.AsyncClient, key: str | None, *, id: int = 1, method: str = "tools/list"
) -> httpx.Response:
    headers = dict(MCP_HEADERS)
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    return await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": id, "method": method}, headers=headers
    )


async def test_over_limit_returns_a_jsonrpc_error_not_a_500(client, workspace_ctx, monkeypatch):
    _set_limit(monkeypatch, 3)
    key = (await make_api_key(client, workspace_ctx))["key"]
    for i in range(3):
        response = await _post_rpc(client, key, id=i + 1)
        assert response.status_code == 200, response.text
        assert "result" in response.json()

    limited = await _post_rpc(client, key, id=9)
    assert limited.status_code == 429
    body = limited.json()
    assert body["jsonrpc"] == "2.0"
    assert body["error"]["code"] == RATE_LIMITED_RPC_CODE
    assert "rate limit" in body["error"]["message"].lower()


async def test_the_window_is_per_key(client, workspace_ctx, monkeypatch):
    _set_limit(monkeypatch, 2)
    first = (await make_api_key(client, workspace_ctx, name="first"))["key"]
    second = (await make_api_key(client, workspace_ctx, name="second"))["key"]
    for i in range(2):
        assert (await _post_rpc(client, first, id=i + 1)).status_code == 200
    assert (await _post_rpc(client, first, id=3)).status_code == 429
    # The sibling key still has its own untouched budget.
    assert (await _post_rpc(client, second)).status_code == 200


async def test_zero_disables_the_limiter(client, workspace_ctx, monkeypatch):
    _set_limit(monkeypatch, 0)
    key = (await make_api_key(client, workspace_ctx))["key"]
    for i in range(8):
        assert (await _post_rpc(client, key, id=i + 1)).status_code == 200


async def test_bearerless_requests_are_not_bucketed(client, workspace_ctx, monkeypatch):
    """No key ⇒ nothing to meter (tools answer with their own auth error);
    the limiter must not invent an anonymous bucket that 429s the login-less."""
    _set_limit(monkeypatch, 1)
    for i in range(4):
        assert (await _post_rpc(client, None, id=i + 1)).status_code == 200
