"""tools/call execution: ask_agent one-shot answers + builtin reads over MCP."""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import session_scope
from app.models.audit import AuditLog
from tests.knowledge.expansion_fixtures import seed_corpus
from tests.mcp_agent.conftest import (
    call_tool,
    error_of,
    make_mcp_agent,
    mint_key,
    result_payload,
)


async def test_ask_agent_happy_path_grounded_answer(client, workspace_ctx):
    await seed_corpus(
        workspace_ctx.id,
        [("Refund policy", "Refunds are available within 30 days of purchase, no questions.")],
    )
    agent_id = await make_mcp_agent(
        workspace_ctx.id,
        system_prompt="MOCK_REPLY: Refunds are available within 30 days. [1]",
    )
    key = await mint_key(workspace_ctx.id)

    response = await call_tool(
        client, agent_id, key, "ask_agent", {"message": "What is the refund policy?"}
    )
    assert response.status_code == 200
    payload, is_error = result_payload(response)
    assert is_error is False
    assert "30 days" in payload["answer"]
    assert payload["citations"], "the [1] marker must resolve to a citation"
    assert payload["citations"][0]["title"] == "Refund policy"
    assert payload["confidence"] > 0


async def test_ask_agent_without_message_is_a_tool_error(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    key = await mint_key(workspace_ctx.id)
    response = await call_tool(client, agent_id, key, "ask_agent", {})
    payload, is_error = result_payload(response)
    assert is_error is True
    assert "message" in payload["text"]


async def test_search_knowledge_over_mcp_uses_the_engine_implementation(client, workspace_ctx):
    await seed_corpus(
        workspace_ctx.id,
        [("Refund policy", "Refunds are available within 30 days of purchase, no questions.")],
    )
    agent_id = await make_mcp_agent(
        workspace_ctx.id, tools=[{"key": "search_knowledge", "policy": "auto"}]
    )
    key = await mint_key(workspace_ctx.id)
    response = await call_tool(
        client, agent_id, key, "search_knowledge", {"query": "refund policy"}
    )
    payload, is_error = result_payload(response)
    assert is_error is False
    assert payload["results"], "seeded corpus must be found"
    assert payload["results"][0]["title"] == "Refund policy"
    assert payload["results"][0]["n"] == 1


async def test_unexposed_tool_call_is_32010(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id, tools=[])
    key = await mint_key(workspace_ctx.id)
    response = await call_tool(client, agent_id, key, "search_knowledge", {"query": "x"})
    error = error_of(response)
    assert error["code"] == -32010
    assert "not exposed" in error["message"]


async def test_tool_calls_are_audited(client, workspace_ctx):
    agent_id = await make_mcp_agent(workspace_ctx.id)
    key = await mint_key(workspace_ctx.id)
    await call_tool(client, agent_id, key, "ask_agent", {"message": "hello there"})
    await call_tool(client, agent_id, key, "nope_tool", {})

    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.workspace_id == workspace_ctx.id,
                        AuditLog.action == "mcp.tool_call",
                    )
                )
            )
            .scalars()
            .all()
        )
    by_tool = {row.meta["tool"]: row for row in rows}
    assert by_tool["ask_agent"].meta["status"] == "ok"
    assert by_tool["ask_agent"].meta["agent_id"] == agent_id
    assert by_tool["ask_agent"].actor_type == "api_key"
    assert by_tool["nope_tool"].meta["status"] == "not_exposed"
