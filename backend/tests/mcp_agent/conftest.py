"""Fixtures for the per-agent MCP channel tests.

The key resolver (`app.mcp.auth.resolve_key`) is owned by a parallel wave agent,
so these tests stay hermetic by monkeypatching the endpoint's ``_resolve`` seam:
raw bearers minted by `mint_key` map to real ``ApiKey`` rows (the approvals table
FKs them) wrapped in a duck-typed view that always carries ``agent_id``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.core.db import session_scope, uuid7
from app.core.permissions import scopes_to_permissions
from app.mcp import agent_endpoint
from app.models.agent import Agent, CustomAction
from app.models.api_key import ApiKey

#: raw bearer -> (api_key_id, agent_id-or-None). Cleared per test by the
#: autouse resolver patch below.
_KEYS: dict[str, tuple[str, str | None]] = {}


@dataclass
class _KeyView:
    """What the endpoint needs from a resolved key, independent of whether the
    parallel agent has landed the ``agent_id`` column on ``ApiKey`` yet."""

    id: str
    name: str
    workspace_id: str
    agent_id: str | None


@pytest.fixture(autouse=True)
def _patch_resolver(monkeypatch: pytest.MonkeyPatch):
    _KEYS.clear()

    async def fake_resolve(session: Any, raw: str, agent_id: str) -> Any:
        # Deliberately does NOT enforce the key↔agent binding: the endpoint's
        # own second guard must catch a mis-bound key even when the resolver
        # is permissive (and does, per the wrong-agent 401 test).
        entry = _KEYS.get(raw)
        if entry is None:
            return None
        key_id, bound_agent_id = entry
        row = await session.get(ApiKey, key_id)
        if row is None or row.revoked_at is not None:
            return None
        return SimpleNamespace(
            api_key=_KeyView(
                id=row.id, name=row.name, workspace_id=row.workspace_id, agent_id=bound_agent_id
            ),
            workspace_id=row.workspace_id,
            # Derived from the row's scopes exactly as the real resolver does —
            # a stub that granted nothing (or everything) would hide whether the
            # endpoint enforces scopes at all.
            permissions=scopes_to_permissions(list(row.scopes)),
        )

    monkeypatch.setattr(agent_endpoint, "_resolve", fake_resolve)


async def mint_key(
    workspace_id: str,
    *,
    agent_id: str | None = None,
    name: str = "MCP key",
    scopes: list[str] | None = None,
) -> str:
    """Create a real ApiKey row and register a raw bearer for it."""
    async with session_scope() as session:
        row = ApiKey(
            workspace_id=workspace_id,
            name=name,
            prefix="sk_stept_test",
            hashed_key=hashlib.sha256(uuid7().encode()).hexdigest(),
            scopes=scopes or ["read", "write"],
        )
        session.add(row)
        await session.commit()
        key_id = row.id
    raw = f"raw-{key_id}"
    _KEYS[raw] = (key_id, agent_id)
    return raw


def mcp_settings(
    *, enabled: bool = True, approval_mode: str = "ask_in_chat", retrieval: dict | None = None
) -> dict[str, Any]:
    return {
        "retrieval": retrieval or {"enabled": True, "k": 6, "source_ids": None},
        "handoff_message": "Let me connect you with a teammate.",
        "guardrails": {"max_tool_calls": 8, "require_citations": False},
        "mcp": {"enabled": enabled, "approval_mode": approval_mode},
    }


async def make_mcp_agent(
    workspace_id: str,
    *,
    enabled: bool = True,
    approval_mode: str = "ask_in_chat",
    tools: list[dict] | None = None,
    system_prompt: str = "",
    settings: dict | None = None,
    name: str = "Fin-alike",
) -> str:
    async with session_scope() as session:
        agent = Agent(
            workspace_id=workspace_id,
            name=name,
            status="live",
            model_ref="mock",
            system_prompt=system_prompt,
            settings=settings or mcp_settings(enabled=enabled, approval_mode=approval_mode),
            tools=tools or [],
        )
        session.add(agent)
        await session.commit()
        return agent.id


TICKET_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}},
    "required": ["subject"],
}
ORDER_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
}


async def make_action(
    workspace_id: str,
    *,
    name: str = "create_ticket",
    method: str = "POST",
    url: str = "https://api.tickets.test/tickets",
    params_schema: dict | None = None,
) -> str:
    async with session_scope() as session:
        action = CustomAction(
            workspace_id=workspace_id,
            name=name,
            description=f"{name} action",
            method=method,
            url=url,
            headers={},
            params_schema=params_schema if params_schema is not None else TICKET_SCHEMA,
        )
        session.add(action)
        await session.commit()
        return action.id


# --- protocol helpers --------------------------------------------------------


def auth(raw_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}"}


async def rpc(
    client: httpx.AsyncClient,
    agent_id: str,
    raw_key: str | None,
    method: str,
    params: dict | None = None,
    *,
    msg_id: Any = 1,
) -> httpx.Response:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        payload["params"] = params
    headers = auth(raw_key) if raw_key else {}
    return await client.post(f"/mcp/agents/{agent_id}", json=payload, headers=headers)


async def call_tool(
    client: httpx.AsyncClient,
    agent_id: str,
    raw_key: str,
    name: str,
    arguments: dict | None = None,
) -> httpx.Response:
    return await rpc(
        client,
        agent_id,
        raw_key,
        "tools/call",
        {"name": name, "arguments": arguments or {}},
    )


def tool_names(list_response: httpx.Response) -> set[str]:
    return {tool["name"] for tool in list_response.json()["result"]["tools"]}


def result_payload(call_response: httpx.Response) -> tuple[dict, bool]:
    """Decode a successful tools/call response into (parsed-json-text, isError)."""
    import json

    body = call_response.json()
    assert "result" in body, body
    block = body["result"]["content"][0]
    assert block["type"] == "text"
    try:
        parsed = json.loads(block["text"])
    except ValueError:
        parsed = {"text": block["text"]}
    return parsed, body["result"]["isError"]


def error_of(response: httpx.Response) -> dict:
    body = response.json()
    assert "error" in body, body
    return body["error"]
