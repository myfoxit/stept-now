"""SLA policy API: CRUD + validation + authz + per-conversation apply/remove."""

from __future__ import annotations

import httpx

from tests.conftest import WorkspaceCtx
from tests.slas.conftest import (
    create_contact_via_db,
    create_conversation_via_api,
    create_inbox_via_api,
)


async def _create_policy(client: httpx.AsyncClient, ctx: WorkspaceCtx, **overrides) -> dict:
    payload = {"name": "Gold", "first_response_minutes": 15, **overrides}
    response = await client.post(f"{ctx.base}/slas", json=payload, headers=ctx.owner_headers)
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_policy_requires_a_threshold(client, workspace_ctx: WorkspaceCtx):
    response = await client.post(
        f"{workspace_ctx.base}/slas",
        json={"name": "Empty"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


async def test_policy_crud_flow(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    policy = await _create_policy(client, ctx, resolution_minutes=240)
    assert policy["first_response_minutes"] == 15
    assert policy["resolution_minutes"] == 240

    listed = await client.get(f"{ctx.base}/slas", headers=ctx.owner_headers)
    assert listed.status_code == 200
    assert [p["id"] for p in listed.json()] == [policy["id"]]

    patched = await client.patch(
        f"{ctx.base}/slas/{policy['id']}",
        json={"name": "Gold v2", "next_response_minutes": 30},
        headers=ctx.owner_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Gold v2"
    assert patched.json()["next_response_minutes"] == 30

    deleted = await client.delete(f"{ctx.base}/slas/{policy['id']}", headers=ctx.owner_headers)
    assert deleted.status_code == 200
    assert (await client.get(f"{ctx.base}/slas", headers=ctx.owner_headers)).json() == []


async def test_patch_cannot_clear_every_threshold(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    policy = await _create_policy(client, ctx)
    response = await client.patch(
        f"{ctx.base}/slas/{policy['id']}",
        json={"first_response_minutes": None},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 422


async def test_non_admins_cannot_manage_policies(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    viewer = await ctx.add_member("viewer@example.com", role="viewer")
    agent = await ctx.add_member("agent@example.com", role="agent")
    body = {"name": "Gold", "first_response_minutes": 15}
    assert (await client.post(f"{ctx.base}/slas", json=body, headers=viewer)).status_code == 403
    # Agents can read but not manage policies (automations:manage required).
    assert (await client.post(f"{ctx.base}/slas", json=body, headers=agent)).status_code == 403
    assert (await client.get(f"{ctx.base}/slas", headers=agent)).status_code == 200


async def test_cross_workspace_policy_404(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    policy = await _create_policy(client, ctx)
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=ctx.owner_headers
    )
    other_base = f"/api/v1/w/{other.json()['id']}"
    response = await client.patch(
        f"{other_base}/slas/{policy['id']}",
        json={"name": "Stolen"},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 404


async def test_apply_and_remove_conversation_sla(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    policy = await _create_policy(client, ctx)
    inbox = await create_inbox_via_api(client, ctx)
    contact_id = await create_contact_via_db(ctx.id)
    conversation = await create_conversation_via_api(
        client, ctx, inbox_id=inbox["id"], contact_id=contact_id
    )

    put = await client.put(
        f"{ctx.base}/conversations/{conversation['id']}/sla",
        json={"sla_policy_id": policy["id"]},
        headers=ctx.owner_headers,
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["policy"]["id"] == policy["id"]
    assert body["status"] == "active"
    assert body["events"] == []

    fetched = await client.get(
        f"{ctx.base}/conversations/{conversation['id']}/sla", headers=ctx.owner_headers
    )
    assert fetched.json()["policy"]["id"] == policy["id"]

    removed = await client.put(
        f"{ctx.base}/conversations/{conversation['id']}/sla",
        json={"sla_policy_id": None},
        headers=ctx.owner_headers,
    )
    assert removed.status_code == 200
    assert removed.json() == {"policy": None, "status": None, "events": []}


async def test_viewer_cannot_apply_sla(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    policy = await _create_policy(client, ctx)
    inbox = await create_inbox_via_api(client, ctx)
    contact_id = await create_contact_via_db(ctx.id)
    conversation = await create_conversation_via_api(
        client, ctx, inbox_id=inbox["id"], contact_id=contact_id
    )
    viewer = await ctx.add_member("viewer2@example.com", role="viewer")
    response = await client.put(
        f"{ctx.base}/conversations/{conversation['id']}/sla",
        json={"sla_policy_id": policy["id"]},
        headers=viewer,
    )
    assert response.status_code == 403
    # Reading the conversation's SLA state only needs conversations:read.
    fetched = await client.get(f"{ctx.base}/conversations/{conversation['id']}/sla", headers=viewer)
    assert fetched.status_code == 200
