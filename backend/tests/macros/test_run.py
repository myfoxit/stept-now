"""Running macros: sequential action execution, placeholder substitution,
per-action error collection, authz and workspace isolation."""

from __future__ import annotations

from tests.conftest import WorkspaceCtx
from tests.macros.conftest import (
    create_contact_via_db,
    create_conversation_via_api,
    create_inbox_via_api,
    create_macro,
    create_tag_via_db,
)


async def _conversation(client, ctx) -> dict:
    inbox = await create_inbox_via_api(client, ctx)
    contact_id = await create_contact_via_db(ctx.id, name="Nina Doe")
    return await create_conversation_via_api(
        client, ctx, inbox_id=inbox["id"], contact_id=contact_id
    )


async def test_run_executes_actions_in_order(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    agent = await ctx.add_member("agent@example.com")  # signup name: "Test User"
    conversation = await _conversation(client, ctx)
    tag_id = await create_tag_via_db(ctx.id, name="vip")
    macro = await create_macro(
        client,
        ctx,
        agent,
        name="Full sweep",
        actions=[
            {"type": "assign_user", "params": {"user_id": "self"}},
            {"type": "set_priority", "params": {"priority": "high"}},
            {"type": "add_tag", "params": {"tag_id": tag_id}},
            {
                "type": "send_reply",
                "params": {"content": "Hi {{contact.name}}, {{agent.name}} here."},
            },
        ],
    )

    run = await client.post(
        f"{ctx.base}/macros/{macro['id']}/run",
        json={"conversation_id": conversation["id"]},
        headers=agent,
    )
    assert run.status_code == 200, run.text
    results = run.json()["results"]
    assert [r["ok"] for r in results] == [True, True, True, True]
    assert [r["action"] for r in results] == [
        "assign_user",
        "set_priority",
        "add_tag",
        "send_reply",
    ]

    detail = (
        await client.get(f"{ctx.base}/conversations/{conversation['id']}", headers=agent)
    ).json()
    assert detail["priority"] == "high"
    assert detail["assignee"] is not None
    assert detail["assignee"]["name"] == "Test User"  # 'self' → the running member
    assert detail["tag_ids"] == [tag_id]

    messages = (
        await client.get(f"{ctx.base}/conversations/{conversation['id']}/messages", headers=agent)
    ).json()["items"]
    reply = next(
        m for m in messages if m["author_name"] == "Test User" and m["visibility"] == "public"
    )
    assert reply["content"] == "Hi Nina Doe, Test User here."
    assert reply["direction"] == "out"
    assert reply["visibility"] == "public"


async def test_run_collects_errors_and_continues(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    conversation = await _conversation(client, ctx)
    macro = await create_macro(
        client,
        ctx,
        ctx.owner_headers,
        name="Half broken",
        actions=[
            {"type": "add_tag", "params": {"tag_id": "00000000-0000-0000-0000-000000000000"}},
            {"type": "set_priority", "params": {"priority": "urgent"}},
        ],
    )

    run = await client.post(
        f"{ctx.base}/macros/{macro['id']}/run",
        json={"conversation_id": conversation["id"]},
        headers=ctx.owner_headers,
    )
    assert run.status_code == 200, run.text
    results = run.json()["results"]
    assert results[0]["action"] == "add_tag"
    assert results[0]["ok"] is False
    assert "Tag not found" in results[0]["error"]
    assert results[1] == {"action": "set_priority", "ok": True, "error": None}

    detail = (
        await client.get(
            f"{ctx.base}/conversations/{conversation['id']}", headers=ctx.owner_headers
        )
    ).json()
    assert detail["priority"] == "urgent"  # later actions still applied
    assert detail["tag_ids"] == []


async def test_run_authz_agent_ok_viewer_403(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    agent = await ctx.add_member("agent@example.com")
    viewer = await ctx.add_member("viewer@example.com", role="viewer")
    conversation = await _conversation(client, ctx)
    macro = await create_macro(
        client,
        ctx,
        ctx.owner_headers,
        visibility="global",
        actions=[{"type": "set_status", "params": {"status": "resolved"}}],
    )

    denied = await client.post(
        f"{ctx.base}/macros/{macro['id']}/run",
        json={"conversation_id": conversation["id"]},
        headers=viewer,
    )
    assert denied.status_code == 403

    allowed = await client.post(
        f"{ctx.base}/macros/{macro['id']}/run",
        json={"conversation_id": conversation["id"]},
        headers=agent,
    )
    assert allowed.status_code == 200
    assert allowed.json()["results"] == [{"action": "set_status", "ok": True, "error": None}]
    detail = (
        await client.get(f"{ctx.base}/conversations/{conversation['id']}", headers=agent)
    ).json()
    assert detail["status"] == "resolved"


async def test_run_cross_workspace_conversation_404(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    macro = await create_macro(client, ctx, ctx.owner_headers, visibility="global")

    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=ctx.owner_headers
    )

    class OtherCtx:
        id = other.json()["id"]
        base = f"/api/v1/w/{id}"
        owner_headers = ctx.owner_headers

    foreign_conversation = await _conversation(client, OtherCtx)

    response = await client.post(
        f"{ctx.base}/macros/{macro['id']}/run",
        json={"conversation_id": foreign_conversation["id"]},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 404
