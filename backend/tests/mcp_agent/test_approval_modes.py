"""The approval_mode matrix on a write custom action, end to end.

respx only intercepts real HTTP transports, so the ASGI test client keeps
working inside ``with respx.mock:`` while the custom action's outbound call is
mocked.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import respx
from sqlalchemy import select

from app.core.db import session_scope, utcnow
from app.models.mcp_approval import McpToolApproval
from tests.mcp_agent.conftest import (
    call_tool,
    error_of,
    make_action,
    make_mcp_agent,
    mint_key,
    result_payload,
)

TICKETS_URL = "https://api.tickets.test/tickets"


async def _write_agent(workspace_id: str, approval_mode: str, *, policy: str = "auto"):
    action_id = await make_action(workspace_id, name="create_ticket", url=TICKETS_URL)
    agent_id = await make_mcp_agent(
        workspace_id,
        approval_mode=approval_mode,
        tools=[
            {"key": f"action:{action_id}", "policy": policy},
            {"key": "search_knowledge", "policy": "auto"},
        ],
    )
    return agent_id


def _mock_tickets() -> respx.Route:
    return respx.post(TICKETS_URL).mock(return_value=httpx.Response(201, json={"ticket": "T-1"}))


async def test_ask_in_chat_write_executes_server_side(client, workspace_ctx):
    agent_id = await _write_agent(workspace_ctx.id, "ask_in_chat")
    key = await mint_key(workspace_ctx.id)
    with respx.mock:
        route = _mock_tickets()
        response = await call_tool(
            client, agent_id, key, "action_create_ticket", {"subject": "Hello"}
        )
    payload, is_error = result_payload(response)
    assert is_error is False
    assert payload["status"] == 201
    assert "T-1" in payload["body"]
    assert route.called


async def test_never_ask_write_executes_without_approval(client, workspace_ctx):
    agent_id = await _write_agent(workspace_ctx.id, "never_ask")
    key = await mint_key(workspace_ctx.id)
    with respx.mock:
        _mock_tickets()
        response = await call_tool(
            client, agent_id, key, "action_create_ticket", {"subject": "Auto"}
        )
    _, is_error = result_payload(response)
    assert is_error is False
    async with session_scope() as session:
        count = len((await session.execute(select(McpToolApproval))).scalars().all())
    assert count == 0


async def test_deny_mode_rejects_writes_but_reads_still_run(client, workspace_ctx):
    agent_id = await _write_agent(workspace_ctx.id, "deny")
    key = await mint_key(workspace_ctx.id)
    response = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": "Nope"})
    assert error_of(response)["code"] == -32011

    read = await call_tool(client, agent_id, key, "search_knowledge", {"query": "anything"})
    _, is_error = result_payload(read)
    assert is_error is False


async def test_ask_in_stept_full_flow_approve_then_execute(client, workspace_ctx):
    agent_id = await _write_agent(workspace_ctx.id, "ask_in_stept")
    key = await mint_key(workspace_ctx.id)

    first = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": "Hi"})
    error = error_of(first)
    assert error["code"] == -32012
    assert error["message"] == "Approval required"
    approval_id = error["data"]["approval_id"]
    assert error["data"]["stept_url"] == f"/w/{workspace_ctx.id}/approvals"

    # same params → the SAME pending approval, no new rows
    retry = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": "Hi"})
    assert error_of(retry)["data"]["approval_id"] == approval_id

    # different params → a different approval
    other = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": "Bye"})
    assert error_of(other)["data"]["approval_id"] != approval_id

    decide = await client.post(
        f"{workspace_ctx.base}/mcp-approvals/{approval_id}/decide",
        json={"decision": "approve"},
        headers=workspace_ctx.owner_headers,
    )
    assert decide.status_code == 200, decide.text
    assert decide.json()["status"] == "approved"

    with respx.mock:
        route = _mock_tickets()
        after = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": "Hi"})
    payload, is_error = result_payload(after)
    assert is_error is False and payload["status"] == 201
    assert route.called
    # the decision keeps governing (approved row is reusable, not consumed)
    async with session_scope() as session:
        row = await session.get(McpToolApproval, approval_id)
        assert row is not None and row.status == "approved"


async def test_ask_in_stept_denied_call_is_32011(client, workspace_ctx):
    agent_id = await _write_agent(workspace_ctx.id, "ask_in_stept")
    key = await mint_key(workspace_ctx.id)
    first = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": "No"})
    approval_id = error_of(first)["data"]["approval_id"]

    decide = await client.post(
        f"{workspace_ctx.base}/mcp-approvals/{approval_id}/decide",
        json={"decision": "deny"},
        headers=workspace_ctx.owner_headers,
    )
    assert decide.status_code == 200
    retry = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": "No"})
    assert error_of(retry)["code"] == -32011


async def test_every_argument_is_part_of_the_approval_identity(client, workspace_ctx):
    """An approval covers the exact call it was shown, underscores included.

    Excluding `_`-prefixed keys (as the old repo did) let a caller get one
    payload approved and then run a different one: the action executor
    substitutes `{_x}` into a URL or body just like any other parameter.
    """
    agent_id = await _write_agent(workspace_ctx.id, "ask_in_stept")
    key = await mint_key(workspace_ctx.id)
    first = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": "Hi"})
    approval_id = error_of(first)["data"]["approval_id"]

    retry = await call_tool(
        client, agent_id, key, "action_create_ticket", {"subject": "Hi", "_meta": "smuggled"}
    )
    assert error_of(retry)["data"]["approval_id"] != approval_id

    # The approver sees the smuggled argument rather than a sanitized version.
    async with session_scope() as session:
        row = await session.get(McpToolApproval, error_of(retry)["data"]["approval_id"])
        assert row is not None
        assert row.tool_input == {"subject": "Hi", "_meta": "smuggled"}


async def test_expired_pending_flips_and_a_new_approval_is_minted(client, workspace_ctx):
    agent_id = await _write_agent(workspace_ctx.id, "ask_in_stept")
    key = await mint_key(workspace_ctx.id)
    first = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": "Old"})
    approval_id = error_of(first)["data"]["approval_id"]

    async with session_scope() as session:
        row = await session.get(McpToolApproval, approval_id)
        assert row is not None
        row.expires_at = utcnow() - timedelta(hours=1)
        await session.commit()

    retry = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": "Old"})
    new_id = error_of(retry)["data"]["approval_id"]
    assert new_id != approval_id
    async with session_scope() as session:
        old = await session.get(McpToolApproval, approval_id)
        assert old is not None and old.status == "expired"


async def test_require_approval_policy_forces_the_gate_even_in_never_ask(client, workspace_ctx):
    agent_id = await _write_agent(workspace_ctx.id, "never_ask", policy="require_approval")
    key = await mint_key(workspace_ctx.id)
    response = await call_tool(client, agent_id, key, "action_create_ticket", {"subject": "Gated"})
    assert error_of(response)["code"] == -32012
