"""Widget intake for page-registered client actions.

Defs can arrive with a message (same transaction as the run trigger — the first
run must already see them) or with page context (registry changes between
messages). Both are normalize-don't-422 surfaces, and both must be unreachable
across visitors.
"""

from __future__ import annotations

import httpx

from app.agents import client_actions
from app.core.db import get_session_factory, session_scope, uuid7
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from tests.widget.conftest import auth_headers, boot, create_widget_setup, identity_payload
from tests.widget.test_copilot import attach_agent, start_thread

INVITE = {
    "name": "invite_teammate",
    "description": "Invite a teammate by email",
    "params": {
        "type": "object",
        "properties": {"email": {"type": "string"}},
        "required": ["email"],
    },
}


async def stored(conversation_id: str) -> tuple[list[dict], bool]:
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        return client_actions.stored_defs(conversation.attributes)


# --- intake with the message --------------------------------------------------


async def test_defs_sent_with_the_first_message_are_stored_before_the_run(client, widget):
    token = (await boot(client, widget.widget_key)).json()["token"]
    created = await client.post(
        "/api/widget/conversations",
        json={"message": "Invite sam for me", "client_actions": [INVITE]},
        headers=auth_headers(token),
    )
    assert created.status_code == 201
    defs, identified = await stored(created.json()["id"])
    assert [d["name"] for d in defs] == ["invite_teammate"]
    assert identified is False  # anonymous boot


async def test_defs_ride_a_follow_up_message_and_replace_the_stored_set(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/messages",
        json={"message": "and now?", "client_actions": [INVITE]},
        headers=auth_headers(token),
    )
    assert response.status_code == 201
    defs, _ = await stored(conversation_id)
    assert [d["name"] for d in defs] == ["invite_teammate"]

    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/messages",
        json={"message": "changed page", "client_actions": []},
        headers=auth_headers(token),
    )
    assert response.status_code == 201
    assert (await stored(conversation_id))[0] == []


async def test_a_message_without_defs_leaves_the_stored_set_alone(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    await client.post(
        f"/api/widget/conversations/{conversation_id}/messages",
        json={"message": "with", "client_actions": [INVITE]},
        headers=auth_headers(token),
    )
    await client.post(
        f"/api/widget/conversations/{conversation_id}/messages",
        json={"message": "without"},
        headers=auth_headers(token),
    )
    defs, _ = await stored(conversation_id)
    assert [d["name"] for d in defs] == ["invite_teammate"]


async def test_an_identified_boot_marks_the_defs_identified(client, widget):
    token = (await boot(client, widget.widget_key, identity=identity_payload("user-42"))).json()[
        "token"
    ]
    created = await client.post(
        "/api/widget/conversations",
        json={"message": "hi", "client_actions": [INVITE]},
        headers=auth_headers(token),
    )
    assert (await stored(created.json()["id"]))[1] is True


# --- intake with page context -------------------------------------------------


async def test_page_context_stores_defs_and_echoes_what_survived(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={
            "url": "https://app.test/",
            "client_actions": [INVITE, {"name": "Bad-Name", "description": "x"}],
        },
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["accepted_actions"] == ["invite_teammate"]
    defs, _ = await stored(conversation_id)
    assert [d["name"] for d in defs] == ["invite_teammate"]


async def test_page_context_without_defs_is_a_no_op_and_empty_clears(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/", "client_actions": [INVITE]},
        headers=auth_headers(token),
    )
    untouched = await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/moved"},
        headers=auth_headers(token),
    )
    assert untouched.json()["accepted_actions"] == []
    assert [d["name"] for d in (await stored(conversation_id))[0]] == ["invite_teammate"]

    cleared = await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/", "client_actions": []},
        headers=auth_headers(token),
    )
    assert cleared.json()["accepted_actions"] == []
    assert (await stored(conversation_id))[0] == []


async def test_defs_cannot_be_written_into_another_visitors_conversation(client, widget):
    _token, conversation_id = await start_thread(client, widget.widget_key)
    other = (await boot(client, widget.widget_key)).json()["token"]
    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://x.test/", "client_actions": [INVITE]},
        headers=auth_headers(other),
    )
    assert response.status_code == 404
    assert [d["name"] for d in (await stored(conversation_id))[0]] == []


# --- pending replay -----------------------------------------------------------


async def park_action_run(
    conversation_id: str, workspace_id: str, agent_id: str, *, name: str = "app_invite_teammate"
) -> tuple[str, str]:
    op_id = uuid7()
    async with session_scope() as session:
        run = AgentRun(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            status="awaiting_client",
            pending_tool_call={
                "id": "call-1",
                "name": name,
                "input": {"email": "sam@acme.io"},
                "client_op_id": op_id,
                "op": "action",
            },
            messages_snapshot=[{"role": "user", "content": "hi", "tool_calls": []}],
        )
        session.add(run)
        await session.commit()
        return run.id, op_id


async def test_pending_replays_an_action_op_with_its_confirm_flag(client, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    agent_id = await attach_agent(conversation_id, widget.workspace_id, settings={})
    await client.post(
        f"/api/widget/conversations/{conversation_id}/page-context",
        json={"url": "https://app.test/", "client_actions": [{**INVITE, "confirm": False}]},
        headers=auth_headers(token),
    )
    run_id, op_id = await park_action_run(conversation_id, widget.workspace_id, agent_id)

    response = await client.get(
        f"/api/widget/conversations/{conversation_id}/copilot/pending",
        headers=auth_headers(token),
    )
    body = response.json()
    assert body["run_id"] == run_id and body["op_id"] == op_id
    assert body["op"] == "action" and body["tool"] == "app_invite_teammate"
    assert body["args"] == {
        "name": "invite_teammate",
        "params": {"email": "sam@acme.io"},
        "confirm": False,
    }


async def test_pending_falls_back_to_confirm_when_the_def_is_gone(client, widget):
    """A def that changed since the park must fail SAFE: card, not auto-run."""
    token, conversation_id = await start_thread(client, widget.widget_key)
    agent_id = await attach_agent(conversation_id, widget.workspace_id, settings={})
    run_id, _op_id = await park_action_run(conversation_id, widget.workspace_id, agent_id)

    response = await client.get(
        f"/api/widget/conversations/{conversation_id}/copilot/pending",
        headers=auth_headers(token),
    )
    body = response.json()
    assert body["run_id"] == run_id
    assert body["args"]["confirm"] is True
    assert body["args"]["name"] == "invite_teammate"


async def test_pending_is_scoped_to_the_owning_visitor(client: httpx.AsyncClient, widget):
    token, conversation_id = await start_thread(client, widget.widget_key)
    agent_id = await attach_agent(conversation_id, widget.workspace_id, settings={})
    await park_action_run(conversation_id, widget.workspace_id, agent_id)
    stranger_setup = await create_widget_setup()
    stranger = (await boot(client, stranger_setup.widget_key)).json()["token"]
    response = await client.get(
        f"/api/widget/conversations/{conversation_id}/copilot/pending",
        headers=auth_headers(stranger),
    )
    assert response.status_code == 404
