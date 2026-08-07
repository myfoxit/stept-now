"""API-keys REST surface: the new agent_id binding."""

from __future__ import annotations

from tests.conftest import bearer, signup
from tests.mcp.conftest import make_agent_row


async def test_create_key_with_agent_id_roundtrip(client, workspace_ctx):
    agent_id = await make_agent_row(workspace_ctx.id)
    response = await client.post(
        f"{workspace_ctx.base}/api-keys",
        json={"name": "Agent key", "scopes": ["read"], "agent_id": agent_id},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["agent_id"] == agent_id
    assert body["key"].startswith("sk_stept_")

    listing = await client.get(
        f"{workspace_ctx.base}/api-keys", headers=workspace_ctx.owner_headers
    )
    assert listing.status_code == 200
    listed = next(k for k in listing.json() if k["id"] == body["id"])
    assert listed["agent_id"] == agent_id

    # Workspace keys keep a null agent_id.
    plain = await client.post(
        f"{workspace_ctx.base}/api-keys",
        json={"name": "Plain", "scopes": ["read"]},
        headers=workspace_ctx.owner_headers,
    )
    assert plain.status_code == 201
    assert plain.json()["agent_id"] is None


async def test_create_key_rejects_cross_workspace_agent(client, workspace_ctx):
    other_auth = await signup(client, "rival-owner@example.com")
    other_ws = (
        await client.post(
            "/api/v1/workspaces", json={"name": "Rival Co"}, headers=bearer(other_auth)
        )
    ).json()
    foreign_agent_id = await make_agent_row(other_ws["id"])

    response = await client.post(
        f"{workspace_ctx.base}/api-keys",
        json={"name": "Sneaky", "scopes": ["read"], "agent_id": foreign_agent_id},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 404, response.text


async def test_agent_bound_key_cannot_call_the_rest_api(client, workspace_ctx):
    """An agent-bound key carries workspace scopes; if REST accepted it, "let
    Claude talk to this one agent" would quietly become full API access."""
    agent_id = await make_agent_row(workspace_ctx.id)
    created = await client.post(
        f"{workspace_ctx.base}/api-keys",
        json={"name": "Agent key", "scopes": ["admin"], "agent_id": agent_id},
        headers=workspace_ctx.owner_headers,
    )
    assert created.status_code == 201
    agent_key = created.json()["key"]

    plain = await client.post(
        f"{workspace_ctx.base}/api-keys",
        json={"name": "Plain", "scopes": ["read"]},
        headers=workspace_ctx.owner_headers,
    )
    workspace_key = plain.json()["key"]

    refused = await client.get(
        f"{workspace_ctx.base}/contacts", headers={"Authorization": f"Bearer {agent_key}"}
    )
    assert refused.status_code == 401, refused.text

    allowed = await client.get(
        f"{workspace_ctx.base}/contacts", headers={"Authorization": f"Bearer {workspace_key}"}
    )
    assert allowed.status_code == 200, allowed.text


async def test_create_key_rejects_unknown_agent(client, workspace_ctx):
    response = await client.post(
        f"{workspace_ctx.base}/api-keys",
        json={
            "name": "Ghost",
            "scopes": ["read"],
            "agent_id": "00000000-0000-0000-0000-000000000000",
        },
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 404, response.text
