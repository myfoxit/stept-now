"""Regression tests for two ways a write could slip past the gates.

Both were found by a security review of this wave and are cheap to reintroduce,
so they get explicit tests rather than a line in a changelog.
"""

from __future__ import annotations

import httpx
import respx

from tests.mcp_agent.conftest import (
    call_tool,
    error_of,
    make_action,
    make_mcp_agent,
    mint_key,
    result_payload,
    rpc,
)

ORDERS_URL = "https://api.orders.test/orders"


async def _agent_with(workspace_id: str, *, name: str, method: str, approval_mode: str) -> str:
    action_id = await make_action(workspace_id, name=name, method=method, url=ORDERS_URL)
    return await make_mcp_agent(
        workspace_id,
        approval_mode=approval_mode,
        tools=[{"key": f"action:{action_id}", "policy": "auto"}],
    )


async def test_read_shaped_name_cannot_smuggle_a_write_past_deny(client, workspace_ctx):
    """A POST action named `get_order` is a write however it is spelled.

    Classifying on the name alone let the author of an action choose whether the
    channel's `deny` mode applied to it — and the caller we are gating is not
    the author.
    """
    agent_id = await _agent_with(
        workspace_ctx.id, name="get_order", method="POST", approval_mode="deny"
    )
    key = await mint_key(workspace_ctx.id)

    with respx.mock:
        route = respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        response = await call_tool(client, agent_id, key, "action_get_order", {"subject": "x"})

    assert error_of(response)["code"] == -32011
    assert not route.called, "the action ran despite the channel denying writes"


async def test_read_shaped_name_with_a_post_is_advertised_as_a_write(client, workspace_ctx):
    agent_id = await _agent_with(
        workspace_ctx.id, name="get_order", method="POST", approval_mode="ask_in_chat"
    )
    key = await mint_key(workspace_ctx.id)

    listing = await rpc(client, agent_id, key, "tools/list")
    tools = {t["name"]: t for t in listing.json()["result"]["tools"]}
    assert tools["action_get_order"]["annotations"] == {
        "readOnlyHint": False,
        "destructiveHint": True,
    }


async def test_genuine_get_action_stays_a_read(client, workspace_ctx):
    """The method gate must not reclassify honest reads as writes."""
    agent_id = await _agent_with(
        workspace_ctx.id, name="get_order", method="GET", approval_mode="deny"
    )
    key = await mint_key(workspace_ctx.id)

    with respx.mock:
        respx.get(ORDERS_URL).mock(return_value=httpx.Response(200, json={"status": "shipped"}))
        response = await call_tool(client, agent_id, key, "action_get_order", {"subject": "x"})

    _payload, is_error = result_payload(response)
    assert is_error is False


async def test_read_only_key_cannot_run_a_write_tool(client, workspace_ctx):
    """Scopes bound this surface exactly as they bound the workspace one."""
    agent_id = await _agent_with(
        workspace_ctx.id, name="create_order", method="POST", approval_mode="never_ask"
    )
    read_key = await mint_key(workspace_ctx.id, scopes=["read"])

    with respx.mock:
        route = respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        response = await call_tool(
            client, agent_id, read_key, "action_create_order", {"subject": "x"}
        )

    error = error_of(response)
    assert error["code"] == -32011
    assert "read-only" in error["message"]
    assert not route.called


async def test_read_only_key_can_still_ask_the_agent(client, workspace_ctx):
    agent_id = await _agent_with(
        workspace_ctx.id, name="create_order", method="POST", approval_mode="never_ask"
    )
    read_key = await mint_key(workspace_ctx.id, scopes=["read"])

    response = await call_tool(client, agent_id, read_key, "ask_agent", {"message": "hello?"})
    _payload, is_error = result_payload(response)
    assert is_error is False


async def test_write_key_runs_the_write_tool(client, workspace_ctx):
    agent_id = await _agent_with(
        workspace_ctx.id, name="create_order", method="POST", approval_mode="never_ask"
    )
    key = await mint_key(workspace_ctx.id, scopes=["read", "write"])

    with respx.mock:
        route = respx.post(ORDERS_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        response = await call_tool(client, agent_id, key, "action_create_order", {"subject": "x"})

    _payload, is_error = result_payload(response)
    assert is_error is False
    assert route.called
