"""The widget half of the in-app assistant: reporting page context + consent,
picking up a deferred page op, and handing its result back.

Every endpoint here can move something in someone else's browser, so the authz
cases (another visitor's conversation, another visitor's run) matter as much as
the happy path.
"""

from __future__ import annotations

import httpx

from app.agents import engine
from app.core.db import get_session_factory, session_scope, uuid7
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from tests.widget.conftest import auth_headers, boot, create_widget_setup

PAGE_CONTROL = {"page_control": {"enabled": True, "allow_actions": True}}


async def start_thread(client: httpx.AsyncClient, widget_key: str) -> tuple[str, str]:
    """Boot a visitor and open one conversation; returns (token, conversation_id)."""
    token = (await boot(client, widget_key)).json()["token"]
    created = await client.post(
        "/api/widget/conversations",
        json={"message": "How do I create an invoice?"},
        headers=auth_headers(token),
    )
    assert created.status_code == 201
    return token, created.json()["id"]


async def attach_agent(
    conversation_id: str, workspace_id: str, *, settings: dict | None = None
) -> str:
    async with session_scope() as session:
        agent = Agent(
            workspace_id=workspace_id,
            name="Guide",
            status="live",
            model_ref="mock",
            system_prompt="",
            temperature=None,
            settings=settings if settings is not None else dict(PAGE_CONTROL),
            tools=[],
        )
        session.add(agent)
        await session.flush()
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        conversation.ai_agent_id = agent.id
        await session.commit()
        return agent.id


async def park_run(conversation_id: str, workspace_id: str, agent_id: str) -> tuple[str, str]:
    """Create a run already waiting on a page op; returns (run_id, op_id)."""
    op_id = uuid7()
    async with session_scope() as session:
        run = AgentRun(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            status="awaiting_client",
            pending_tool_call={
                "id": "call-1",
                "name": "page_snapshot",
                "input": {},
                "client_op_id": op_id,
                "op": "snapshot",
            },
            messages_snapshot=[{"role": "user", "content": "hi", "tool_calls": []}],
        )
        session.add(run)
        await session.commit()
        return run.id, op_id


# --- page context + consent -------------------------------------------------


async def test_page_context_is_stored_on_the_conversation(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/billing", "title": "Billing", "path": "/billing"},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation.attributes["page_url"] == "https://app.test/billing"
        assert conversation.attributes["page_title"] == "Billing"
        assert conversation.attributes["page_path"] == "/billing"


async def test_page_context_reports_no_page_control_without_an_agent(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/"},
        headers=auth_headers(token),
    )
    assert response.json() == {
        "ok": True,
        "page_control": False,
        "allow_actions": False,
        "accepted_actions": [],
    }


async def test_consent_unlocks_actions_and_is_echoed_back(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    await attach_agent(conversation_id, widget.workspace_id)

    offered = await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/"},
        headers=auth_headers(token),
    )
    assert offered.json() == {
        "ok": True,
        "page_control": True,
        "allow_actions": False,
        "accepted_actions": [],
    }

    consented = await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/", "allow_actions": True},
        headers=auth_headers(token),
    )
    assert consented.json()["allow_actions"] is True


async def test_a_navigation_does_not_silently_revoke_consent(client, widget):
    """The loader pushes context on every SPA URL change; that must not reset consent."""
    token, conversation_id = await start_thread(client, widget.widget_key)
    await attach_agent(conversation_id, widget.workspace_id)
    await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/", "allow_actions": True},
        headers=auth_headers(token),
    )
    moved = await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/invoices/new"},
        headers=auth_headers(token),
    )
    assert moved.json()["allow_actions"] is True


async def test_revoking_consent_switches_page_control_off(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    await attach_agent(conversation_id, widget.workspace_id)
    revoked = await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/", "allow_actions": False},
        headers=auth_headers(token),
    )
    assert revoked.json() == {
        "ok": True,
        "page_control": False,
        "allow_actions": False,
        "accepted_actions": [],
    }


async def test_page_context_needs_a_widget_token(client, widget):
    _token, conversation_id = await start_thread(client, widget.widget_key)
    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/"},
    )
    assert response.status_code == 401


async def test_a_visitor_cannot_touch_another_visitors_conversation(client, widget):
    _token_a, conversation_a = await start_thread(client, widget.widget_key)
    other = await create_widget_setup()
    token_b = (await boot(client, other.widget_key)).json()["token"]
    response = await client.post(
        f"/api/widget/conversations/{conversation_a}/page-context",
        json={"url": "https://evil.test/"},
        headers=auth_headers(token_b),
    )
    assert response.status_code == 404


# --- pending op -------------------------------------------------------------


async def test_pending_is_null_when_nothing_is_waiting(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    response = await client.get(
        f"/api/widget/conversations/{conversation_id}/copilot/pending",
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json() is None


async def test_a_reloaded_widget_can_pick_up_the_parked_op(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    agent_id = await attach_agent(conversation_id, widget.workspace_id)
    run_id, op_id = await park_run(conversation_id, widget.workspace_id, agent_id)

    response = await client.get(
        f"/api/widget/conversations/{conversation_id}/copilot/pending",
        headers=auth_headers(token),
    )
    body = response.json()
    assert body["run_id"] == run_id
    assert body["op_id"] == op_id
    assert body["op"] == "snapshot"
    assert body["tool"] == "page_snapshot"


async def test_an_already_answered_op_is_not_offered_again(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    agent_id = await attach_agent(conversation_id, widget.workspace_id)
    run_id, op_id = await park_run(conversation_id, widget.workspace_id, agent_id)
    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        run.pending_tool_call = {**run.pending_tool_call, "result": {"ok": True}}
        await session.commit()

    response = await client.get(
        f"/api/widget/conversations/{conversation_id}/copilot/pending",
        headers=auth_headers(token),
    )
    assert response.json() is None
    assert op_id  # the op existed; it is simply no longer outstanding


# --- op results -------------------------------------------------------------


async def test_submitting_a_result_resumes_the_run(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    agent_id = await attach_agent(conversation_id, widget.workspace_id)
    run_id, op_id = await park_run(conversation_id, widget.workspace_id, agent_id)

    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/copilot/result",
        json={
            "run_id": run_id,
            "op_id": op_id,
            "result": {"ok": True, "url": "https://app.test/", "elements": "[0]<button>"},
        },
        headers=auth_headers(token),
    )
    assert response.json() == {"ok": True, "status": "resumed"}


async def test_a_stale_result_is_acknowledged_but_ignored(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    agent_id = await attach_agent(conversation_id, widget.workspace_id)
    run_id, _op_id = await park_run(conversation_id, widget.workspace_id, agent_id)

    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/copilot/result",
        json={"run_id": run_id, "op_id": "some-old-op", "result": {"ok": True}},
        headers=auth_headers(token),
    )
    assert response.json() == {"ok": True, "status": "ignored"}


async def test_a_result_for_another_conversations_run_is_rejected(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    agent_id = await attach_agent(conversation_id, widget.workspace_id)
    run_id, op_id = await park_run(conversation_id, widget.workspace_id, agent_id)

    _token2, other_conversation = await start_thread(client, widget.widget_key)
    response = await client.post(
        f"/api/widget/conversations/{other_conversation}/copilot/result",
        json={"run_id": run_id, "op_id": op_id, "result": {"ok": True}},
        headers=auth_headers(token),
    )
    assert response.status_code == 404


async def test_an_oversized_result_is_truncated_before_it_reaches_the_model(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    agent_id = await attach_agent(conversation_id, widget.workspace_id)
    run_id, op_id = await park_run(conversation_id, widget.workspace_id, agent_id)

    await client.post(
        f"/api/widget/conversations/{conversation_id}/copilot/result",
        json={
            "run_id": run_id,
            "op_id": op_id,
            "result": {"ok": True, "elements": "x" * 200_000},
        },
        headers=auth_headers(token),
    )
    async with get_session_factory()() as session:
        run = await session.get(AgentRun, run_id)
        stored = run.pending_tool_call["result"]["elements"]
    assert len(stored) == 24_000


async def test_the_widget_cannot_answer_a_run_that_is_not_waiting(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    agent_id = await attach_agent(conversation_id, widget.workspace_id)
    async with session_scope() as session:
        run = AgentRun(
            workspace_id=widget.workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            status="completed",
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/copilot/result",
        json={"run_id": run_id, "op_id": "x", "result": {"ok": True}},
        headers=auth_headers(token),
    )
    assert response.json()["status"] == "ignored"
    assert engine.CLIENT_OP_TIMEOUT_SECONDS > 0
