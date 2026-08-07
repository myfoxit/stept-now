"""REST surface for MCP approvals: listing, deciding, authz, expiry, audit."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.core.db import session_scope, utcnow
from app.models.audit import AuditLog
from app.models.mcp_approval import McpToolApproval
from tests.conftest import bearer
from tests.mcp_agent.conftest import call_tool, error_of, make_action, make_mcp_agent, mint_key


async def _pending_approval(client, workspace_ctx, *, subject: str = "Hi") -> tuple[str, str]:
    """Drive the MCP channel to park one pending approval; returns (agent_id, approval_id)."""
    action_id = await make_action(workspace_ctx.id, name="create_ticket")
    agent_id = await make_mcp_agent(
        workspace_ctx.id,
        approval_mode="ask_in_stept",
        tools=[{"key": f"action:{action_id}", "policy": "auto"}],
        name="Approver bait",
    )
    key = await mint_key(workspace_ctx.id)
    response = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": subject})
    return agent_id, error_of(response)["data"]["approval_id"]


async def test_list_pending_then_all(client, workspace_ctx):
    agent_id, approval_id = await _pending_approval(client, workspace_ctx)

    listing = await client.get(
        f"{workspace_ctx.base}/mcp-approvals", headers=workspace_ctx.owner_headers
    )
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert [row["id"] for row in rows] == [approval_id]
    assert rows[0]["status"] == "pending"
    assert rows[0]["agent_id"] == agent_id
    assert rows[0]["agent_name"] == "Approver bait"
    assert rows[0]["tool_key"] == "action_create_ticket"
    assert rows[0]["tool_input"] == {"subject": "Hi"}

    decide = await client.post(
        f"{workspace_ctx.base}/mcp-approvals/{approval_id}/decide",
        json={"decision": "deny"},
        headers=workspace_ctx.owner_headers,
    )
    assert decide.status_code == 200
    assert decide.json()["status"] == "denied"
    assert decide.json()["decided_by"] is not None

    pending_again = await client.get(
        f"{workspace_ctx.base}/mcp-approvals", headers=workspace_ctx.owner_headers
    )
    assert pending_again.json() == []
    everything = await client.get(
        f"{workspace_ctx.base}/mcp-approvals?status=all", headers=workspace_ctx.owner_headers
    )
    assert [row["status"] for row in everything.json()] == ["denied"]


async def test_viewer_role_lacks_ai_approve(client, workspace_ctx):
    _, approval_id = await _pending_approval(client, workspace_ctx)
    viewer = await workspace_ctx.add_member("viewer@example.com", role="viewer")

    listing = await client.get(f"{workspace_ctx.base}/mcp-approvals", headers=viewer)
    assert listing.status_code == 403
    decide = await client.post(
        f"{workspace_ctx.base}/mcp-approvals/{approval_id}/decide",
        json={"decision": "approve"},
        headers=viewer,
    )
    assert decide.status_code == 403


async def test_cross_workspace_decide_is_404(client, workspace_ctx):
    _, approval_id = await _pending_approval(client, workspace_ctx)
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Second"}, headers=bearer(workspace_ctx.owner_auth)
    )
    other_id = other.json()["id"]
    response = await client.post(
        f"/api/v1/w/{other_id}/mcp-approvals/{approval_id}/decide",
        json={"decision": "approve"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 404


async def test_deciding_twice_is_409(client, workspace_ctx):
    _, approval_id = await _pending_approval(client, workspace_ctx)
    first = await client.post(
        f"{workspace_ctx.base}/mcp-approvals/{approval_id}/decide",
        json={"decision": "approve"},
        headers=workspace_ctx.owner_headers,
    )
    assert first.status_code == 200
    second = await client.post(
        f"{workspace_ctx.base}/mcp-approvals/{approval_id}/decide",
        json={"decision": "deny"},
        headers=workspace_ctx.owner_headers,
    )
    assert second.status_code == 409


async def test_listing_flips_overdue_pending_to_expired(client, workspace_ctx):
    _, approval_id = await _pending_approval(client, workspace_ctx)
    async with session_scope() as session:
        row = await session.get(McpToolApproval, approval_id)
        assert row is not None
        row.expires_at = utcnow() - timedelta(minutes=5)
        await session.commit()

    pending = await client.get(
        f"{workspace_ctx.base}/mcp-approvals", headers=workspace_ctx.owner_headers
    )
    assert pending.json() == []
    everything = await client.get(
        f"{workspace_ctx.base}/mcp-approvals?status=all", headers=workspace_ctx.owner_headers
    )
    assert [row["status"] for row in everything.json()] == ["expired"]
    # deciding an expired approval conflicts
    decide = await client.post(
        f"{workspace_ctx.base}/mcp-approvals/{approval_id}/decide",
        json={"decision": "approve"},
        headers=workspace_ctx.owner_headers,
    )
    assert decide.status_code == 409


async def test_decisions_are_audited(client, workspace_ctx):
    _, approval_id = await _pending_approval(client, workspace_ctx)
    await client.post(
        f"{workspace_ctx.base}/mcp-approvals/{approval_id}/decide",
        json={"decision": "approve"},
        headers=workspace_ctx.owner_headers,
    )
    async with session_scope() as session:
        row = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.workspace_id == workspace_ctx.id,
                    AuditLog.action == "mcp_approval.decide",
                )
            )
        ).scalar_one()
    assert row.target_id == approval_id
    assert row.meta["status"] == "approved"
    assert row.meta["tool"] == "action_create_ticket"
