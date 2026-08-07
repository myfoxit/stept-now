"""tools/list exposure: computed per request from agent config."""

from __future__ import annotations

from tests.mcp_agent.conftest import (
    ORDER_SCHEMA,
    make_action,
    make_mcp_agent,
    mint_key,
    rpc,
    tool_names,
)


async def test_exposure_matrix(client, workspace_ctx):
    """search_knowledge auto + read/write actions in; disabled + page_* out."""
    write_action = await make_action(workspace_ctx.id, name="create_ticket")
    read_action = await make_action(
        workspace_ctx.id,
        name="lookup-order",
        method="GET",
        url="https://api.orders.test/orders/{order_id}",
        params_schema=ORDER_SCHEMA,
    )
    agent_id = await make_mcp_agent(
        workspace_ctx.id,
        tools=[
            {"key": "search_knowledge", "policy": "auto"},
            {"key": "find_guide", "policy": "disabled"},
            {"key": "page_snapshot", "policy": "auto"},
            {"key": f"action:{write_action}", "policy": "auto"},
            {"key": f"action:{read_action}", "policy": "auto"},
        ],
    )
    key = await mint_key(workspace_ctx.id)
    response = await rpc(client, agent_id, key, "tools/list")
    assert response.status_code == 200

    names = tool_names(response)
    assert names == {"ask_agent", "search_knowledge", "action_create_ticket", "action_lookup_order"}

    by_name = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    # ask_in_chat (default): write descriptors carry destructive hints + confirm suffix
    write = by_name["action_create_ticket"]
    assert write["annotations"] == {"readOnlyHint": False, "destructiveHint": True}
    assert write["description"].endswith("Confirm with the user before calling.")
    assert write["inputSchema"]["required"] == ["subject"]
    # a read-prefixed action stays a read
    read = by_name["action_lookup_order"]
    assert read["annotations"] == {"readOnlyHint": True, "destructiveHint": False}
    assert "Confirm with the user" not in read["description"]
    assert by_name["search_knowledge"]["annotations"]["readOnlyHint"] is True


async def test_ask_agent_is_always_exposed_even_with_no_tools(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id, tools=[])
    key = await mint_key(workspace_ctx.id)
    response = await rpc(client, agent_id, key, "tools/list")
    assert tool_names(response) == {"ask_agent"}
    tool = response.json()["result"]["tools"][0]
    assert tool["inputSchema"]["required"] == ["message"]


async def test_confirm_suffix_only_in_ask_in_chat_mode(client, workspace_ctx):
    action_id = await make_action(workspace_ctx.id, name="create_ticket")
    agent_id = await make_mcp_agent(
        workspace_ctx.id,
        approval_mode="never_ask",
        tools=[{"key": f"action:{action_id}", "policy": "auto"}],
    )
    key = await mint_key(workspace_ctx.id)
    response = await rpc(client, agent_id, key, "tools/list")
    tool = next(
        t for t in response.json()["result"]["tools"] if t["name"] == "action_create_ticket"
    )
    assert "Confirm with the user" not in tool["description"]
    assert tool["annotations"]["destructiveHint"] is True


async def test_agent_settings_schema_carries_mcp_block(client, workspace_ctx):
    """The McpChannelSettings block flows through the existing agent CRUD."""
    response = await client.post(
        f"{workspace_ctx.base}/ai/agents",
        json={
            "name": "Channel test",
            "settings": {"mcp": {"enabled": True, "approval_mode": "never_ask"}},
        },
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    settings = response.json()["settings"]
    assert settings["mcp"] == {"enabled": True, "approval_mode": "never_ask"}

    # default is off + ask_in_chat
    response = await client.post(
        f"{workspace_ctx.base}/ai/agents",
        json={"name": "Defaults"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.json()["settings"]["mcp"] == {"enabled": False, "approval_mode": "ask_in_chat"}
