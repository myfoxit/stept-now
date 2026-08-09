"""Developer-registered client actions: def intake → plan merge → deferred run.

What matters here is the trust chain, not "does a function run": a def the page
never registered must be unreachable, an identity-gated def must be invisible to
an anonymous visitor, `approval` must land in the existing human gate, a
looping model must run out of rope, and a confirm card nobody answers must time
out on the generous clock — all across the park/resume cycle.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update

from app.agents import client_actions, engine
from app.agents import tools as tool_registry
from app.agents.tools import POLICY_REQUIRE_APPROVAL
from app.core.db import session_scope, utcnow, uuid7
from app.models.agent import Agent, CustomAction
from app.models.agent_run import AgentRun, AgentStep
from app.models.conversation import Conversation
from tests.agents.conftest import (
    conversation_with_message,
    get_pending_approval,
    get_run,
    get_steps,
    make_agent,
    run_now,
    step_kinds,
)
from tests.conftest import drain_tasks

INVITE = {
    "name": "invite_teammate",
    "description": "Invite a teammate by email",
    "params": {
        "type": "object",
        "properties": {"email": {"type": "string"}},
        "required": ["email"],
    },
}


async def set_client_actions(
    conversation_id: str, defs: list[dict], *, identified: bool = True
) -> None:
    """Store defs the way the widget intake does (see api/widget/conversations)."""
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        attributes = dict(conversation.attributes or {})
        attributes[client_actions.ATTR_KEY] = client_actions.stored_block(
            client_actions.normalize_defs(defs), identified=identified
        )
        conversation.attributes = attributes
        await session.commit()


async def resume_with(run_id: str, result: dict, *, op_id: str | None = None) -> None:
    """Submit a client result and let the queued resume finish the run."""
    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        pending = run.pending_tool_call or {}
        await engine.submit_client_result(
            session, run, op_id=op_id or pending["client_op_id"], result=result
        )
        await session.commit()
    await drain_tasks()


async def resolve_plan(actx, agent_id: str, conversation_id: str) -> tool_registry.ToolPlan:
    async with session_scope() as session:
        agent = await session.get(Agent, agent_id)
        conversation = await session.get(Conversation, conversation_id)
        assert agent is not None and conversation is not None
        return await tool_registry.resolve_agent_tools(
            session, actx.workspace_id, agent, conversation=conversation
        )


# --- normalization -----------------------------------------------------------


def test_normalize_drops_malformed_defs_and_keeps_the_last_registration():
    defs = client_actions.normalize_defs(
        [
            {"name": "Bad-Name", "description": "x"},  # invalid name
            {"name": "no_description"},  # missing description
            {"name": "big_schema", "description": "x", "params": {"pad": "y" * 5000}},
            {"name": "ok", "description": "first", "confirm": False},
            {"name": "ok", "description": "second"},  # replaces the first
            "not a dict",
        ]
    )
    assert [d["name"] for d in defs] == ["ok"]
    assert defs[0]["description"] == "second"
    assert defs[0]["confirm"] is True  # replacement did not inherit the old flags


def test_normalize_caps_count_and_total_payload():
    many = [{"name": f"a{n}", "description": "d"} for n in range(30)]
    assert len(client_actions.normalize_defs(many)) == client_actions.MAX_DEFS
    fat = [
        {"name": f"fat{n}", "description": "d" * client_actions.MAX_DESCRIPTION} for n in range(30)
    ]
    kept = client_actions.normalize_defs(fat)
    assert 0 < len(kept) < 30  # stopped at the stored-payload budget, not the count


def test_op_for_carries_name_params_and_confirm():
    op = client_actions.op_for(
        {"name": "invite_teammate", "confirm": False}, {"email": "sam@acme.io"}
    )
    assert op == {
        "op": "action",
        "args": {"name": "invite_teammate", "params": {"email": "sam@acme.io"}, "confirm": False},
    }
    assert client_actions.op_for({"name": "x"}, {})["args"]["confirm"] is True  # default ON


# --- plan merge --------------------------------------------------------------


async def test_registered_actions_enter_the_plan_from_the_conversation(actx):
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(actx, "hello")
    await set_client_actions(conversation_id, [INVITE])

    plan = await resolve_plan(actx, agent_id, conversation_id)
    assert "app_invite_teammate" in plan.client
    assert plan.client_action_defs["app_invite_teammate"]["name"] == "invite_teammate"
    spec = next(s for s in plan.specs if s.name == "app_invite_teammate")
    assert spec.input_schema["required"] == ["email"]


async def test_actions_are_absent_without_defs_and_when_switched_off(actx):
    conversation_id, _ = await conversation_with_message(actx, "hello")
    plain = await resolve_plan(actx, await make_agent(actx), conversation_id)
    assert not plain.client_action_defs

    await set_client_actions(conversation_id, [INVITE])
    off = await make_agent(actx, settings={"client_actions": {"enabled": False}})
    plan = await resolve_plan(actx, off, conversation_id)
    assert not plan.client_action_defs
    assert "app_invite_teammate" not in {s.name for s in plan.specs}


async def test_identity_gated_defs_are_withheld_from_anonymous_visitors(actx):
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(actx, "hello")
    gated = {**INVITE, "requires_identity": True}
    await set_client_actions(conversation_id, [gated], identified=False)
    assert not (await resolve_plan(actx, agent_id, conversation_id)).client_action_defs

    await set_client_actions(conversation_id, [gated], identified=True)
    plan = await resolve_plan(actx, agent_id, conversation_id)
    assert "app_invite_teammate" in plan.client


async def test_a_workspace_custom_action_beats_a_page_registered_name(actx):
    """A page must not be able to shadow a tool the workspace configured."""
    async with session_scope() as session:
        action = CustomAction(
            workspace_id=actx.workspace_id,
            name="app_invite_teammate",
            description="Workspace-configured HTTP action",
            method="POST",
            url="https://api.acme.io/invite",
            params_schema={},
        )
        session.add(action)
        await session.commit()
        action_id = action.id
    agent_id = await make_agent(actx, tools=[{"key": f"action:{action_id}", "policy": "auto"}])
    conversation_id, _ = await conversation_with_message(actx, "hello")
    await set_client_actions(conversation_id, [INVITE])

    plan = await resolve_plan(actx, agent_id, conversation_id)
    assert plan.action_ids.get("app_invite_teammate") == action_id
    assert "app_invite_teammate" not in plan.client_action_defs
    assert "app_invite_teammate" not in plan.client


# --- engine: defer + resume --------------------------------------------------


async def test_an_action_call_parks_the_run_with_the_wire_op(actx):
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(
        actx, 'Invite sam [[tool:app_invite_teammate {"email": "sam@acme.io"}]]'
    )
    await set_client_actions(conversation_id, [INVITE])
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    run = await get_run(run_id)
    assert run.status == "awaiting_client"
    assert run.pending_tool_call["name"] == "app_invite_teammate"
    assert run.pending_tool_call["op"] == "action"
    steps = await get_steps(run_id)
    request = next(s for s in steps if s.kind == "client_request")
    assert request.name == "app_invite_teammate"
    assert request.output["args"] == {
        "name": "invite_teammate",
        "params": {"email": "sam@acme.io"},
        "confirm": True,
    }


async def test_the_handler_result_resumes_the_run_to_a_reply(actx):
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(
        actx, 'Invite sam [[tool:app_invite_teammate {"email": "sam@acme.io"}]]'
    )
    await set_client_actions(conversation_id, [INVITE])
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    await resume_with(run_id, {"ok": True, "result": "invited sam@acme.io"})

    run = await get_run(run_id)
    assert run.status == "completed"
    kinds = await step_kinds(run_id)
    assert "tool_result" in kinds and kinds[-1] == "final_reply"


async def test_a_declined_confirm_reads_as_an_error_result(actx):
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(
        actx, 'Invite sam [[tool:app_invite_teammate {"email": "sam@acme.io"}]]'
    )
    await set_client_actions(conversation_id, [INVITE])
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    await resume_with(run_id, {"ok": False, "declined": True, "error": "the person declined"})

    run = await get_run(run_id)
    assert run.status in ("completed", "handed_off")  # answered, never stranded
    result = next(s for s in await get_steps(run_id) if s.kind == "tool_result")
    assert result.output.get("declined") is True


async def test_schema_validation_answers_locally_without_a_browser_round_trip(actx):
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(
        actx, "Invite someone [[tool:app_invite_teammate {}]]"
    )
    await set_client_actions(conversation_id, [INVITE])
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    run = await get_run(run_id)
    assert run.status != "awaiting_client"
    steps = await get_steps(run_id)
    assert not [s for s in steps if s.kind == "client_request"]
    rejected = next(s for s in steps if s.kind == "tool_result")
    assert "email" in rejected.output["error"]


async def test_the_per_run_cap_stops_a_looping_model(actx):
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(
        actx, 'Again [[tool:app_invite_teammate {"email": "sam@acme.io"}]]'
    )
    await set_client_actions(conversation_id, [INVITE])

    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        if conversation.status == "open":
            conversation.status = "pending"
        run = AgentRun(
            workspace_id=actx.workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            status="queued",
            trigger_message_id=message_id,
        )
        session.add(run)
        await session.flush()
        for ord_ in range(client_actions.MAX_ACTION_CALLS):
            session.add(
                AgentStep(
                    workspace_id=actx.workspace_id,
                    run_id=run.id,
                    ord=ord_ + 1,
                    kind="client_request",
                    name="app_invite_teammate",
                    input={},
                    output={},
                )
            )
        run_id = run.id
        await engine.execute_run(session, run)
        await session.commit()

    run_row = await get_run(run_id)
    assert run_row.status != "awaiting_client"
    rejected = [
        s
        for s in await get_steps(run_id)
        if s.kind == "tool_result" and "already run" in str(s.output.get("error", ""))
    ]
    assert rejected, "the cap must answer the call locally"


async def test_an_approval_def_lands_in_the_existing_human_gate(actx):
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(
        actx, 'Refund it [[tool:app_refund_order {"order_id": "o_1"}]]'
    )
    await set_client_actions(
        conversation_id,
        [
            {
                "name": "refund_order",
                "description": "Refund an order",
                "params": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
                "approval": True,
            }
        ],
    )
    plan = await resolve_plan(actx, agent_id, conversation_id)
    assert plan.policy["app_refund_order"] == POLICY_REQUIRE_APPROVAL

    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    assert (await get_run(run_id)).status == "awaiting_approval"
    approval = await get_pending_approval(run_id)
    assert approval.tool_key == "app_refund_order"


# --- sweep -------------------------------------------------------------------


async def _park_action_run(actx, agent_id: str, conversation_id: str) -> str:
    async with session_scope() as session:
        run = AgentRun(
            workspace_id=actx.workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            status="awaiting_client",
            pending_tool_call={
                "id": "call-1",
                "name": "app_invite_teammate",
                "input": {"email": "sam@acme.io"},
                "client_op_id": uuid7(),
                "op": "action",
            },
            messages_snapshot=[{"role": "user", "content": "hi", "tool_calls": []}],
        )
        session.add(run)
        await session.commit()
        return run.id


async def _age_run(run_id: str, seconds: int) -> None:
    async with session_scope() as session:
        await session.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(updated_at=utcnow() - timedelta(seconds=seconds))
        )
        await session.commit()


async def test_the_sweep_gives_a_confirm_card_a_person_sized_clock(actx):
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(actx, "hello")
    run_id = await _park_action_run(actx, agent_id, conversation_id)

    # Past the page-op timeout but within the confirm window: leave it alone.
    await _age_run(run_id, engine.CLIENT_OP_TIMEOUT_SECONDS + 30)
    async with session_scope() as session:
        assert await engine.sweep_stale_client_waits(session) == []
        await session.commit()
    assert (await get_run(run_id)).status == "awaiting_client"

    # Past the confirm window: resumed with a "did not confirm" result.
    await _age_run(run_id, engine.ACTION_CONFIRM_TIMEOUT_SECONDS + 30)
    async with session_scope() as session:
        swept = await engine.sweep_stale_client_waits(session)
        assert [r.id for r in swept] == [run_id]
        await session.commit()
    pending = (await get_run(run_id)).pending_tool_call
    assert pending["result"]["error"] == "the person did not confirm the action in time"
