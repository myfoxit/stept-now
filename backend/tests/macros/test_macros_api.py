"""Macros: CRUD + personal/global visibility rules + validation + authz."""

from __future__ import annotations

from tests.conftest import WorkspaceCtx
from tests.macros.conftest import create_macro


async def test_listing_shows_global_plus_own_personal(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    agent_a = await ctx.add_member("agent-a@example.com")
    agent_b = await ctx.add_member("agent-b@example.com")

    global_macro = await create_macro(
        client, ctx, ctx.owner_headers, name="Team macro", visibility="global"
    )
    mine = await create_macro(client, ctx, agent_a, name="My macro")
    await create_macro(client, ctx, agent_b, name="Their macro")

    listed = await client.get(f"{ctx.base}/macros", headers=agent_a)
    assert listed.status_code == 200
    names = {m["name"] for m in listed.json()}
    assert names == {"Team macro", "My macro"}
    assert {m["id"] for m in listed.json()} == {global_macro["id"], mine["id"]}

    # The owner sees the global macro but nobody's personal macros.
    owner_names = {
        m["name"]
        for m in (await client.get(f"{ctx.base}/macros", headers=ctx.owner_headers)).json()
    }
    assert owner_names == {"Team macro"}


async def test_crud_own_personal_macro(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    agent = await ctx.add_member("agent@example.com")
    macro = await create_macro(client, ctx, agent, name="Snooze it")

    patched = await client.patch(
        f"{ctx.base}/macros/{macro['id']}",
        json={"name": "Snooze + tag", "actions": [{"type": "add_tag", "params": {"tag": "vip"}}]},
        headers=agent,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Snooze + tag"
    assert patched.json()["actions"] == [{"type": "add_tag", "params": {"tag": "vip"}}]

    deleted = await client.delete(f"{ctx.base}/macros/{macro['id']}", headers=agent)
    assert deleted.status_code == 200
    assert (await client.get(f"{ctx.base}/macros", headers=agent)).json() == []


async def test_other_members_personal_macro_is_invisible(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    agent_a = await ctx.add_member("agent-a@example.com")
    agent_b = await ctx.add_member("agent-b@example.com")
    macro = await create_macro(client, ctx, agent_a, name="Private")

    # Not listed, not patchable, not deletable, not runnable — 404 like the listing.
    patched = await client.patch(
        f"{ctx.base}/macros/{macro['id']}", json={"name": "Hijack"}, headers=agent_b
    )
    assert patched.status_code == 404
    assert (
        await client.delete(f"{ctx.base}/macros/{macro['id']}", headers=agent_b)
    ).status_code == 404
    run = await client.post(
        f"{ctx.base}/macros/{macro['id']}/run", json={"conversation_id": "x"}, headers=agent_b
    )
    assert run.status_code == 404


async def test_viewer_cannot_create_macros(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    viewer = await ctx.add_member("viewer@example.com", role="viewer")
    response = await client.post(
        f"{ctx.base}/macros",
        json={
            "name": "Nope",
            "actions": [{"type": "set_priority", "params": {"priority": "low"}}],
        },
        headers=viewer,
    )
    assert response.status_code == 403
    # Viewers can still read the (global) macro list.
    assert (await client.get(f"{ctx.base}/macros", headers=viewer)).status_code == 200


async def test_macro_validation_rejects_bad_payloads(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    unknown_action = await client.post(
        f"{ctx.base}/macros",
        json={"name": "Bad", "actions": [{"type": "explode", "params": {}}]},
        headers=ctx.owner_headers,
    )
    assert unknown_action.status_code == 422
    empty_actions = await client.post(
        f"{ctx.base}/macros",
        json={"name": "Bad", "actions": []},
        headers=ctx.owner_headers,
    )
    assert empty_actions.status_code == 422
    bad_visibility = await client.post(
        f"{ctx.base}/macros",
        json={
            "name": "Bad",
            "actions": [{"type": "set_priority", "params": {"priority": "low"}}],
            "visibility": "everyone",
        },
        headers=ctx.owner_headers,
    )
    assert bad_visibility.status_code == 422


async def test_cross_workspace_macro_404(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    macro = await create_macro(client, ctx, ctx.owner_headers, visibility="global")
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=ctx.owner_headers
    )
    other_base = f"/api/v1/w/{other.json()['id']}"
    response = await client.patch(
        f"{other_base}/macros/{macro['id']}", json={"name": "Steal"}, headers=ctx.owner_headers
    )
    assert response.status_code == 404
