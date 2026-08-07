"""Fixtures/helpers for the workspace MCP surface (`/mcp`).

Tests drive the real streamable-HTTP transport over the ASGI client:
JSON-RPC POSTs to /mcp (stateless + json_response mode, so no session
handshake is needed). Tool payloads come back JSON-encoded in
result.content[0].text — `call_tool` parses that.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.db import session_scope, uuid7
from app.core.events import Actor
from app.models.agent import Agent
from app.models.contact import Contact
from app.models.inbox import Inbox
from app.services import conversations as conversations_service
from tests.conftest import drain_tasks

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _headers(key: str | None) -> dict[str, str]:
    headers = dict(MCP_HEADERS)
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    return headers


async def rpc(
    client: httpx.AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    key: str | None = None,
    id: int = 1,
) -> dict[str, Any]:
    """One JSON-RPC request against /mcp; returns the parsed response body."""
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    response = await client.post("/mcp", json=body, headers=_headers(key))
    assert response.status_code == 200, response.text
    return response.json()


async def call_tool(
    client: httpx.AsyncClient,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    key: str | None = None,
) -> Any:
    """tools/call → the tool's plain dict/list payload.

    The SDK mirrors the return value into ``structuredContent`` — dicts as-is,
    lists wrapped as ``{"result": [...]}`` (text content splits a list into one
    block per item, so it is not a faithful transport of the whole value).
    """
    body = await rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, key=key)
    assert "result" in body, body
    result = body["result"]
    assert result.get("isError") is False, result
    structured = result.get("structuredContent")
    if structured is not None:
        if isinstance(structured, dict) and list(structured.keys()) == ["result"]:
            return structured["result"]
        return structured
    return json.loads(result["content"][0]["text"])


async def list_tool_names(client: httpx.AsyncClient) -> set[str]:
    body = await rpc(client, "tools/list")
    return {tool["name"] for tool in body["result"]["tools"]}


async def read_resource(client: httpx.AsyncClient, uri: str, *, key: str | None = None) -> str:
    body = await rpc(client, "resources/read", {"uri": uri}, key=key)
    return body["result"]["contents"][0]["text"]


# --- workspace-side seeding --------------------------------------------------


async def make_api_key(
    client: httpx.AsyncClient,
    ctx,
    *,
    scopes: list[str] | None = None,
    agent_id: str | None = None,
    name: str = "MCP key",
) -> dict[str, Any]:
    """Create an API key via the REST API; returns the created body (with .key)."""
    payload: dict[str, Any] = {"name": name, "scopes": scopes or ["read"]}
    if agent_id is not None:
        payload["agent_id"] = agent_id
    response = await client.post(f"{ctx.base}/api-keys", json=payload, headers=ctx.owner_headers)
    assert response.status_code == 201, response.text
    return response.json()


async def seed_document(
    client: httpx.AsyncClient,
    ctx,
    *,
    title: str = "Refund policy",
    content: str = (
        "Refunds are processed within 5 business days. "
        "Contact billing to start a refund for any annual plan."
    ),
) -> tuple[str, str]:
    """Create a text source + pasted document and run ingestion → (source_id, document_id)."""
    source = await client.post(
        f"{ctx.base}/knowledge/sources",
        json={"type": "text", "name": "Docs", "config": {}},
        headers=ctx.owner_headers,
    )
    assert source.status_code == 201, source.text
    source_id = source.json()["id"]
    document = await client.post(
        f"{ctx.base}/knowledge/sources/{source_id}/documents",
        json={"title": title, "content": content},
        headers=ctx.owner_headers,
    )
    assert document.status_code == 201, document.text
    await drain_tasks()
    return source_id, document.json()["id"]


async def make_agent_row(workspace_id: str, *, name: str = "Support agent") -> str:
    """Insert + commit an AI Agent row directly (the builder API belongs to
    another domain — MCP tests only need the row to bind keys to)."""
    async with session_scope() as session:
        agent = Agent(
            workspace_id=workspace_id,
            name=name,
            status="live",
            model_ref="mock",
            system_prompt="",
            settings={},
            tools=[],
        )
        session.add(agent)
        await session.flush()
        return agent.id


async def seed_conversation(
    workspace_id: str,
    *,
    subject: str | None = "Billing question",
    contact_name: str = "Nina Doe",
    contact_email: str = "nina@example.com",
    message: str = "Hello, I need help with billing",
) -> str:
    """Inbox + contact + conversation + one inbound message, committed."""
    async with session_scope() as session:
        inbox = Inbox(
            workspace_id=workspace_id,
            name="Widget",
            channel_type="widget",
            config={},
            widget_key=f"wk_{uuid7()}",
        )
        contact = Contact(workspace_id=workspace_id, name=contact_name, email=contact_email)
        session.add_all([inbox, contact])
        await session.flush()
        actor = Actor(type="contact", id=contact.id, label=contact.name)
        conversation = await conversations_service.create_conversation(
            session, inbox=inbox, contact=contact, subject=subject, actor=actor
        )
        await conversations_service.add_message(
            session,
            conversation,
            direction="in",
            author_type="contact",
            author_id=contact.id,
            author_name=contact.name,
            content=message,
            actor=actor,
        )
        return conversation.id
