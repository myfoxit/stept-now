"""Cross-request key isolation on the workspace ``/mcp`` surface.

The transport reads the caller's bearer from a CONTEXTVAR that an ASGI shim
(`app.mcp.mount.McpAuthShim`) sets before handing the scope to the MCP SDK, and
tools read it much later, inside a task the SDK's session manager starts. If
those contexts were ever shared between requests, one client's key — and with
it, one tenant's data — would leak into another client's tool call.

These tests pin the guarantee by forcing the worst-case interleaving: both
requests are parked on a barrier *between* the shim's capture and the tool's
read of the contextvar, so a shared context would make the later ``set`` win
for both.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.mcp import tools_knowledge
from tests.conftest import bearer, signup
from tests.mcp.conftest import MCP_HEADERS, make_api_key, seed_document

#: Safety valve: a barrier that never releases must fail the test, not hang it.
GATE_TIMEOUT = 15.0


async def _other_workspace(client: httpx.AsyncClient, email: str, name: str) -> Any:
    """A second tenant with the same shape as the ``workspace_ctx`` fixture."""
    auth = await signup(client, email)
    workspace = (
        await client.post("/api/v1/workspaces", json={"name": name}, headers=bearer(auth))
    ).json()
    return SimpleNamespace(
        id=workspace["id"],
        base=f"/api/v1/w/{workspace['id']}",
        owner_headers=bearer(auth),
    )


#: The 2026-07-28 single-exchange dispatch wants the client envelope in _meta.
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}


async def _search(
    client: httpx.AsyncClient, key: str | None, query: str, protocol_version: str | None = None
) -> Any:
    headers = dict(MCP_HEADERS)
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    params: dict[str, Any] = {"name": "search_knowledge", "arguments": {"query": query}}
    if protocol_version is not None:
        headers["MCP-Protocol-Version"] = protocol_version
        headers["mcp-method"] = "tools/call"
        headers["mcp-name"] = "search_knowledge"
        params["_meta"] = MODERN_META
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "result" in body, body
    return body["result"]["structuredContent"]["result"]


def _park_on_barrier(monkeypatch: pytest.MonkeyPatch, parties: int) -> None:
    """Suspend every in-flight tool between shim capture and contextvar read.

    Patching the *tool module's* name is what matters: ``tools_knowledge``
    imported the resolver by value, and the resolver is what calls
    ``request_raw_key()`` → ``current_authorization()``.
    """
    barrier = asyncio.Barrier(parties)
    original = tools_knowledge.resolve_request_key

    async def gated(session: Any) -> Any:
        async with asyncio.timeout(GATE_TIMEOUT):
            await barrier.wait()
        return await original(session)

    monkeypatch.setattr(tools_knowledge, "resolve_request_key", gated)


async def test_simultaneous_requests_each_resolve_their_own_key(client, workspace_ctx, monkeypatch):
    """Two overlapping tool calls with different keys must see different tenants."""
    _, doc_a = await seed_document(
        client, workspace_ctx, title="Acme refunds", content="Acme refunds take five days."
    )
    key_a = (await make_api_key(client, workspace_ctx, scopes=["read"]))["key"]

    other = await _other_workspace(client, "isolation-owner@example.com", "Globex")
    _, doc_b = await seed_document(
        client, other, title="Globex refunds", content="Globex refunds take five days."
    )
    key_b = (await make_api_key(client, other, scopes=["read"]))["key"]

    _park_on_barrier(monkeypatch, 2)
    results_a, results_b = await asyncio.gather(
        _search(client, key_a, "refunds"), _search(client, key_b, "refunds")
    )

    assert [r["document_id"] for r in results_a] == [doc_a]
    assert [r["document_id"] for r in results_b] == [doc_b]


async def test_unauthenticated_request_never_borrows_an_in_flight_key(
    client, workspace_ctx, monkeypatch
):
    """A header-less call overlapping an authenticated one stays unauthenticated."""
    _, doc = await seed_document(client, workspace_ctx, title="Acme refunds")
    key = (await make_api_key(client, workspace_ctx, scopes=["read"]))["key"]
    monkeypatch.delenv("STEPT_API_KEY", raising=False)

    _park_on_barrier(monkeypatch, 2)
    authenticated, anonymous = await asyncio.gather(
        _search(client, key, "refunds"), _search(client, None, "refunds")
    )

    assert [r["document_id"] for r in authenticated] == [doc]
    assert anonymous == [{"error": "Authentication required. Provide a valid API key."}]


async def test_isolation_holds_on_the_modern_protocol_dispatch(client, workspace_ctx, monkeypatch):
    """The SDK routes ``MCP-Protocol-Version: 2026-07-28`` down a different
    serving path (single-exchange, not the stateless session manager) — the
    contextvar must be per-request there too."""
    _, doc_a = await seed_document(client, workspace_ctx, title="Acme refunds")
    key_a = (await make_api_key(client, workspace_ctx, scopes=["read"]))["key"]

    other = await _other_workspace(client, "modern-owner@example.com", "Globex")
    _, doc_b = await seed_document(client, other, title="Globex refunds")
    key_b = (await make_api_key(client, other, scopes=["read"]))["key"]

    _park_on_barrier(monkeypatch, 2)
    results_a, results_b = await asyncio.gather(
        _search(client, key_a, "refunds", protocol_version="2026-07-28"),
        _search(client, key_b, "refunds", protocol_version="2026-07-28"),
    )

    assert [r["document_id"] for r in results_a] == [doc_a]
    assert [r["document_id"] for r in results_b] == [doc_b]
