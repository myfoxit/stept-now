"""HTTP surface: agent + action CRUD (with authz and masked headers), the sandbox
test endpoint (dry-run), run traces, and the copilot."""

from __future__ import annotations

import httpx
import respx
from sqlalchemy import func, select

from app.core.db import session_scope
from app.models.agent import CustomAction
from app.models.agent_run import AgentRun
from tests.agents.conftest import (
    conversation_with_message,
    get_run,
    make_agent,
    run_now,
    seed_rag_docs,
)

AGENT_BODY = {"name": "Helpdesk", "status": "live", "model_ref": "mock", "system_prompt": "Hi"}


async def test_agent_crud(actx, client):
    created = await client.post(
        f"{actx.base}/ai/agents", json=AGENT_BODY, headers=actx.owner_headers
    )
    assert created.status_code == 201, created.text
    agent = created.json()
    assert agent["name"] == "Helpdesk" and agent["status"] == "live"

    listed = await client.get(f"{actx.base}/ai/agents", headers=actx.owner_headers)
    assert listed.status_code == 200
    assert any(a["id"] == agent["id"] for a in listed.json())

    fetched = await client.get(f"{actx.base}/ai/agents/{agent['id']}", headers=actx.owner_headers)
    assert fetched.status_code == 200

    patched = await client.patch(
        f"{actx.base}/ai/agents/{agent['id']}",
        json={"status": "off", "tools": [{"key": "close_conversation", "policy": "auto"}]},
        headers=actx.owner_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "off"
    assert patched.json()["tools"] == [{"key": "close_conversation", "policy": "auto"}]

    deleted = await client.delete(
        f"{actx.base}/ai/agents/{agent['id']}", headers=actx.owner_headers
    )
    assert deleted.status_code == 200
    assert (
        await client.get(f"{actx.base}/ai/agents/{agent['id']}", headers=actx.owner_headers)
    ).status_code == 404


async def test_agent_crud_authz(actx, client):
    viewer = await actx.wc.add_member("viewer@x.io", "viewer")
    agent = await actx.wc.add_member("agent@x.io", "agent")

    # ai:read lets both roles list agents.
    assert (await client.get(f"{actx.base}/ai/agents", headers=viewer)).status_code == 200
    assert (await client.get(f"{actx.base}/ai/agents", headers=agent)).status_code == 200
    # ai:manage is required to create — neither viewer nor agent has it.
    assert (
        await client.post(f"{actx.base}/ai/agents", json=AGENT_BODY, headers=viewer)
    ).status_code == 403
    assert (
        await client.post(f"{actx.base}/ai/agents", json=AGENT_BODY, headers=agent)
    ).status_code == 403


async def test_action_crud_masks_header_values(actx, client):
    body = {
        "name": "lookup_order",
        "method": "GET",
        "url": "https://api.orders.test/orders/{order_id}",
        "headers": {"Authorization": "Bearer super-secret"},
        "params_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    }
    created = await client.post(f"{actx.base}/ai/actions", json=body, headers=actx.owner_headers)
    assert created.status_code == 201, created.text
    action = created.json()
    assert action["header_names"] == ["Authorization"]
    assert "headers" not in action  # values never leave the service
    assert "super-secret" not in created.text

    # Encrypted at rest.
    async with session_scope() as session:
        row = await session.get(CustomAction, action["id"])
        assert row is not None and row.headers["Authorization"] != "Bearer super-secret"

    listed = await client.get(f"{actx.base}/ai/actions", headers=actx.owner_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["header_names"] == ["Authorization"]


async def test_action_crud_authz(actx, client):
    agent = await actx.wc.add_member("agent2@x.io", "agent")
    body = {"name": "act", "url": "https://api.x.test/a", "params_schema": {}}
    # ai:manage required for actions (even listing).
    assert (await client.get(f"{actx.base}/ai/actions", headers=agent)).status_code == 403
    assert (
        await client.post(f"{actx.base}/ai/actions", json=body, headers=agent)
    ).status_code == 403


async def test_action_test_endpoint(actx, client):
    body = {
        "name": "lookup_order",
        "method": "GET",
        "url": "https://api.orders.test/orders/{order_id}",
        "params_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    }
    created = await client.post(f"{actx.base}/ai/actions", json=body, headers=actx.owner_headers)
    action_id = created.json()["id"]

    with respx.mock:
        respx.get("https://api.orders.test/orders/A9").mock(
            return_value=httpx.Response(200, json={"status": "shipped"})
        )
        resp = await client.post(
            f"{actx.base}/ai/actions/{action_id}/test",
            json={"params": {"order_id": "A9"}},
            headers=actx.owner_headers,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert resp.json()["status"] == 200

    # Validation failure is reported, not thrown.
    missing = await client.post(
        f"{actx.base}/ai/actions/{action_id}/test",
        json={"params": {}},
        headers=actx.owner_headers,
    )
    assert missing.status_code == 200
    assert missing.json()["ok"] is False
    assert "missing required" in missing.json()["error"]


async def test_sandbox_dry_run_makes_no_writes(actx, client):
    agent_id = await make_agent(actx)
    resp = await client.post(
        f"{actx.base}/ai/agents/{agent_id}/test",
        json={"message": '[[tool:handoff_to_human {"reason": "test"}]]'},
        headers=actx.owner_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "handed_off"
    assert any(step["output"].get("dry_run") for step in body["steps"])

    # No runs / no conversations were persisted by the sandbox.
    async with session_scope() as session:
        run_count = (
            await session.execute(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.workspace_id == actx.workspace_id)
            )
        ).scalar_one()
    assert run_count == 0


async def test_sandbox_search_returns_citations(actx, client):
    await seed_rag_docs(actx.workspace_id)
    agent_id = await make_agent(actx)
    resp = await client.post(
        f"{actx.base}/ai/agents/{agent_id}/test",
        json={"message": '[[tool:search_knowledge {"query": "install the widget"}]]'},
        headers=actx.owner_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["citations"], "sandbox search should return real citations"
    assert body["status"] == "completed"


async def test_runs_list_and_detail(actx, client):
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(actx, "hi there")
    run_id = await run_now(actx, agent_id, conversation_id)
    assert (await get_run(run_id)).status == "completed"

    listed = await client.get(f"{actx.base}/ai/runs", headers=actx.owner_headers)
    assert listed.status_code == 200
    page = listed.json()
    assert page["total"] >= 1
    assert any(item["id"] == run_id for item in page["items"])
    assert page["items"][0]["agent_name"]

    filtered = await client.get(
        f"{actx.base}/ai/runs?status=completed&agent_id={agent_id}", headers=actx.owner_headers
    )
    assert filtered.status_code == 200
    assert all(i["status"] == "completed" for i in filtered.json()["items"])

    detail = await client.get(f"{actx.base}/ai/runs/{run_id}", headers=actx.owner_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["run"]["id"] == run_id
    assert [s["kind"] for s in body["steps"]] == ["llm_call", "final_reply"]


async def test_runs_authz_viewer_can_read(actx, client):
    viewer = await actx.wc.add_member("viewer3@x.io", "viewer")
    assert (await client.get(f"{actx.base}/ai/runs", headers=viewer)).status_code == 200


async def test_copilot_suggest_with_citations(actx, client):
    await seed_rag_docs(actx.workspace_id)
    conversation_id, _ = await conversation_with_message(actx, "How do I install the chat widget?")
    resp = await client.post(
        f"{actx.base}/ai/copilot/suggest",
        json={"conversation_id": conversation_id},
        headers=actx.owner_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"]
    assert body["citations"], "copilot should ground the draft in sources"


async def test_copilot_authz(actx, client):
    viewer = await actx.wc.add_member("viewer4@x.io", "viewer")
    conversation_id, _ = await conversation_with_message(actx, "hello")
    # viewer lacks conversations:write
    assert (
        await client.post(
            f"{actx.base}/ai/copilot/suggest",
            json={"conversation_id": conversation_id},
            headers=viewer,
        )
    ).status_code == 403
