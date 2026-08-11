"""In-app guidance: guide lookup, the client-tool contract, and the
defer-to-the-browser half of the engine loop.

The interesting behaviour here is not "does a tool run" — it is that a tool the
server *cannot* run parks the whole conversation safely, resumes on exactly the
result it asked for, refuses a stale one, and never strands the visitor when the
browser goes away.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.agents import engine, page_tools
from app.agents.guides import keywords, search_guides
from app.core.db import session_scope, utcnow, uuid7
from app.core.errors import ConflictError
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.checklist import Checklist
from app.models.conversation import Conversation
from app.models.tour import Tour
from tests.agents.conftest import (
    add_contact_message,
    conversation_with_message,
    get_run,
    get_steps,
    make_agent,
    public_messages,
    run_now,
    step_kinds,
)
from tests.conftest import drain_tasks

PAGE_CONTROL = {
    "retrieval": {"enabled": True, "k": 6, "source_ids": None},
    "guardrails": {"max_tool_calls": 8, "require_citations": False},
    "page_control": {"enabled": True, "allow_actions": True},
}
SHOW_ONLY = {**PAGE_CONTROL, "page_control": {"enabled": True, "allow_actions": False}}


# --- helpers ----------------------------------------------------------------


async def allow_page_control(conversation_id: str, *, consent: bool = True, url: str | None = None):
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        attributes = dict(conversation.attributes or {})
        attributes["page_control_consent"] = consent
        if url:
            attributes["page_url"] = url
        conversation.attributes = attributes
        await session.commit()


async def make_tour(
    workspace_id: str,
    *,
    name: str,
    description: str = "",
    status: str = "live",
    steps: list[dict] | None = None,
    url_pattern: str | None = None,
) -> str:
    async with session_scope() as session:
        tour = Tour(
            workspace_id=workspace_id,
            name=name,
            description=description,
            status=status,
            trigger=(
                {"type": "url_match", "url_pattern": url_pattern}
                if url_pattern
                else {"type": "manual"}
            ),
            audience={"type": "all"},
            steps=steps or [{"id": "s1", "selector": "#a", "title": "Step one", "body": ""}],
            theme={"accent": "#6366f1"},
        )
        session.add(tour)
        await session.commit()
        return tour.id


async def latest_run(conversation_id: str) -> AgentRun:
    async with session_scope() as session:
        return (
            await session.execute(
                select(AgentRun)
                .where(AgentRun.conversation_id == conversation_id)
                .order_by(AgentRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one()


async def resume_with(run_id: str, result: dict, *, op_id: str | None = None) -> None:
    """Submit a page-op result and let the queued resume finish the run.

    Draining rather than calling `execute_run` directly: `submit_client_result`
    enqueues the resume itself, and doing both would run the loop twice from the
    same snapshot.
    """
    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        pending = run.pending_tool_call or {}
        await engine.submit_client_result(
            session, run, op_id=op_id or pending["client_op_id"], result=result
        )
        await session.commit()
    await drain_tasks()


# --- guide search -----------------------------------------------------------


def test_keywords_drops_stopwords_and_punctuation():
    assert keywords("How do I create an invoice?") == {"create", "invoice"}


async def test_search_guides_ranks_name_matches_first(actx):
    await make_tour(actx.workspace_id, name="Create an invoice")
    await make_tour(
        actx.workspace_id,
        name="Team settings",
        steps=[{"id": "s1", "selector": "#a", "title": "Find the invoice tab", "body": ""}],
    )
    async with session_scope() as session:
        matches = await search_guides(session, actx.workspace_id, "how do I create an invoice")
    assert [match.name for match in matches] == ["Create an invoice", "Team settings"]


async def test_search_guides_skips_drafts(actx):
    await make_tour(actx.workspace_id, name="Create an invoice", status="draft")
    async with session_scope() as session:
        assert await search_guides(session, actx.workspace_id, "create invoice") == []


async def test_search_guides_returns_nothing_for_an_unrelated_question(actx):
    await make_tour(actx.workspace_id, name="Create an invoice")
    async with session_scope() as session:
        assert await search_guides(session, actx.workspace_id, "reset my password") == []


async def test_search_guides_boosts_a_guide_for_the_current_page(actx):
    await make_tour(actx.workspace_id, name="Invoice basics")
    await make_tour(actx.workspace_id, name="Invoice basics elsewhere", url_pattern="*/billing*")
    async with session_scope() as session:
        matches = await search_guides(
            session, actx.workspace_id, "invoice", url="https://app.test/billing/new"
        )
    assert matches[0].name == "Invoice basics elsewhere"
    assert matches[0].url_pattern == "*/billing*"


async def test_search_guides_url_match_is_case_insensitive(actx):
    await make_tour(actx.workspace_id, name="Invoice help", url_pattern="*/billing*")
    async with session_scope() as session:
        matches = await search_guides(
            session, actx.workspace_id, "invoice", url="https://app.test/BILLING/new"
        )
    assert matches[0].url_pattern == "*/billing*"


async def test_search_guides_includes_live_checklists(actx):
    async with session_scope() as session:
        session.add(
            Checklist(
                workspace_id=actx.workspace_id,
                name="Invoice onboarding",
                status="live",
                items=[{"id": "i1", "title": "Send your first invoice"}],
                trigger={},
                audience={"type": "all"},
                theme={},
                launcher={},
            )
        )
        await session.commit()
    async with session_scope() as session:
        matches = await search_guides(session, actx.workspace_id, "invoice onboarding")
    assert [(m.kind, m.name) for m in matches] == [("checklist", "Invoice onboarding")]


async def test_search_guides_never_crosses_a_workspace(actx):
    await make_tour(actx.workspace_id, name="Create an invoice")
    async with session_scope() as session:
        assert await search_guides(session, uuid7(), "invoice") == []


# --- tool exposure ----------------------------------------------------------


def test_client_tools_withheld_unless_page_control_enabled():
    assert page_tools.client_tools_available({}, {}) == (False, False)
    assert page_tools.client_tools_available({"page_control": {"enabled": False}}, {}) == (
        False,
        False,
    )


def test_client_tools_offered_read_only_without_consent():
    offer, mutating = page_tools.client_tools_available(PAGE_CONTROL, {})
    assert (offer, mutating) == (True, False)
    names = {spec.name for spec in page_tools.client_tool_specs(allow_mutating=mutating)}
    assert page_tools.PAGE_SNAPSHOT in names
    assert page_tools.SHOW_STEPS in names
    assert page_tools.PAGE_ACT not in names


def test_consent_unlocks_mutating_tools_only_when_the_agent_allows_actions():
    consented = {"page_control_consent": True}
    assert page_tools.client_tools_available(PAGE_CONTROL, consented) == (True, True)
    assert page_tools.client_tools_available(SHOW_ONLY, consented) == (True, False)


def test_explicit_refusal_switches_everything_off():
    assert page_tools.client_tools_available(PAGE_CONTROL, {"page_control_consent": False}) == (
        False,
        False,
    )


async def test_page_tools_are_absent_from_the_plan_by_default(actx):
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(actx, "hello")
    async with session_scope() as session:
        agent = await session.get(Agent, agent_id)
        conversation = await session.get(Conversation, conversation_id)
        plan = await engine.tool_registry.resolve_agent_tools(
            session, actx.workspace_id, agent, conversation=conversation
        )
    assert plan.client == set()
    assert "find_guide" in {spec.name for spec in plan.specs}


async def test_page_tools_enter_the_plan_with_consent(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, _ = await conversation_with_message(actx, "hello")
    await allow_page_control(conversation_id)
    async with session_scope() as session:
        agent = await session.get(Agent, agent_id)
        conversation = await session.get(Conversation, conversation_id)
        plan = await engine.tool_registry.resolve_agent_tools(
            session, actx.workspace_id, agent, conversation=conversation
        )
    assert page_tools.PAGE_ACT in plan.client
    assert page_tools.CLIENT_TOOLS <= plan.client


# --- op translation + validation -------------------------------------------


def test_op_for_normalizes_arguments():
    assert page_tools.op_for(page_tools.PAGE_ACT, {"index": "3", "kind": "fill", "text": "42"}) == {
        "op": "act",
        "args": {"index": 3, "kind": "fill", "text": "42"},
    }
    assert (
        page_tools.op_for(page_tools.PAGE_ACT, {"index": 1, "kind": "teleport"})["args"]["kind"]
        == "click"
    )
    assert page_tools.op_for(page_tools.PAGE_SCROLL, {"dir": "sideways"})["args"]["dir"] == "down"


def test_op_for_rejects_a_server_side_tool():
    with pytest.raises(ValueError, match="not a client tool"):
        page_tools.op_for("search_knowledge", {})


def test_validate_catches_calls_worth_refusing_locally():
    assert page_tools.validate(page_tools.PAGE_ACT, {}) is not None
    assert page_tools.validate(page_tools.PAGE_ACT, {"index": -1}) is not None
    assert page_tools.validate(page_tools.PAGE_ACT, {"index": 2, "kind": "fill"}) is not None
    assert page_tools.validate(page_tools.PAGE_ACT, {"index": 2, "kind": "click"}) is None
    assert page_tools.validate(page_tools.PAGE_FIND, {"query": " "}) is not None
    assert page_tools.validate(page_tools.SHOW_GUIDE, {}) is not None
    assert page_tools.validate(page_tools.SHOW_STEPS, {"steps": []}) is not None
    assert page_tools.validate(page_tools.SHOW_STEPS, {"steps": [{"title": "Click Save"}]}) is None


def test_show_steps_drops_untitled_steps_and_caps_the_walkthrough():
    op = page_tools.op_for(
        page_tools.SHOW_STEPS,
        {"steps": [{"title": "One", "index": 3}, {"body": "no title"}, {"title": "Two"}]},
    )
    assert [step["title"] for step in op["args"]["steps"]] == ["One", "Two"]
    assert op["args"]["steps"][0]["index"] == 3
    too_many = {"steps": [{"title": f"Step {n}"} for n in range(9)]}
    assert page_tools.validate(page_tools.SHOW_STEPS, too_many) is not None


# --- engine: defer + resume -------------------------------------------------


async def test_a_page_tool_parks_the_run_and_pushes_the_op(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, "What is on my screen? [[tool:page_snapshot {}]]"
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    run = await get_run(run_id)
    assert run.status == "awaiting_client"
    assert run.pending_tool_call["name"] == "page_snapshot"
    assert run.pending_tool_call["client_op_id"]
    assert run.messages_snapshot, "the loop must be resumable from the DB alone"
    assert await step_kinds(run_id) == ["llm_call", "client_request"]
    # Nothing was said to the visitor yet — the guide is mid-flight.
    outbound = [m for m in await public_messages(conversation_id) if m.direction == "out"]
    assert outbound == []


async def test_the_widget_result_resumes_the_run_and_reaches_a_reply(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, "What is on my screen? [[tool:page_snapshot {}]]"
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    await resume_with(
        run_id,
        {"ok": True, "url": "https://app.test/billing", "elements": '[0]<button name="Save">'},
    )

    run = await get_run(run_id)
    assert run.status == "completed"
    assert run.pending_tool_call is None
    assert run.messages_snapshot is None
    assert await step_kinds(run_id) == [
        "llm_call",
        "client_request",
        "tool_result",
        "llm_call",
        "final_reply",
    ]
    result_step = next(step for step in await get_steps(run_id) if step.kind == "tool_result")
    assert result_step.output["url"] == "https://app.test/billing"


async def test_a_failed_page_op_comes_back_as_a_tool_error(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, 'Click it [[tool:page_act {"index": 4}]]'
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    await resume_with(run_id, {"ok": False, "error": "no element at index 4"})

    run = await get_run(run_id)
    assert run.status == "completed"  # the model saw the error and answered anyway
    result_step = next(step for step in await get_steps(run_id) if step.kind == "tool_result")
    assert result_step.output["error"] == "no element at index 4"


async def test_a_stale_op_result_is_refused(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, "Look [[tool:page_snapshot {}]]"
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        with pytest.raises(ConflictError, match="different step"):
            await engine.submit_client_result(session, run, op_id="not-the-op", result={"ok": True})


async def test_a_result_for_a_run_that_is_not_waiting_is_refused(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, _ = await conversation_with_message(actx, "hello")
    run_id = await run_now(actx, agent_id, conversation_id)
    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        with pytest.raises(ConflictError, match="not waiting"):
            await engine.submit_client_result(session, run, op_id="x", result={"ok": True})


async def test_a_malformed_page_call_never_reaches_the_browser(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, 'Click [[tool:page_act {"kind": "click"}]]'
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    run = await get_run(run_id)
    assert run.status == "completed", "validation is answered locally, the loop continues"
    kinds = await step_kinds(run_id)
    assert "client_request" not in kinds
    error_step = next(step for step in await get_steps(run_id) if step.kind == "tool_result")
    assert "index is required" in error_step.output["error"]


async def test_mutating_ops_are_capped_per_run(actx, monkeypatch):
    monkeypatch.setattr(page_tools, "MAX_MUTATING_OPS", 1)
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx,
        'Do it [[tool:page_act {"index": 0}]] then again [[tool:page_act {"index": 1}]]',
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    await resume_with(run_id, {"ok": True, "url": "https://app.test/", "elements": "[0]<button>"})

    steps = await get_steps(run_id)
    requests = [step for step in steps if step.kind == "client_request"]
    assert len(requests) == 1, "the second act must be refused, not sent"
    refusal = [
        step
        for step in steps
        if step.kind == "tool_result"
        and "already changed this page" in str(step.output.get("error"))
    ]
    assert refusal


async def test_an_unanswered_op_is_swept_and_the_run_finishes(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, "Look [[tool:page_snapshot {}]]"
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    # Age the parked run past the timeout, then sweep.
    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        run.updated_at = utcnow() - timedelta(seconds=engine.CLIENT_OP_TIMEOUT_SECONDS + 5)
        await session.commit()
    async with session_scope() as session:
        swept = await engine.sweep_stale_client_waits(session)
        await session.commit()
    assert [run.id for run in swept] == [run_id]

    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        await engine.execute_run(session, run)
        await session.commit()
    run = await get_run(run_id)
    assert run.status == "completed"
    result_step = next(step for step in await get_steps(run_id) if step.kind == "tool_result")
    assert "timed out" in result_step.output["error"]


async def test_the_sweep_leaves_a_freshly_parked_run_alone(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, "Look [[tool:page_snapshot {}]]"
    )
    await allow_page_control(conversation_id)
    await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    async with session_scope() as session:
        assert await engine.sweep_stale_client_waits(session) == []


async def test_a_sandbox_run_dry_runs_page_tools_instead_of_waiting(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    async with session_scope() as session:
        agent = await session.get(Agent, agent_id)
        result = await engine.run_sandbox(
            session, agent, message="Look [[tool:page_snapshot {}]] and tell me"
        )
    assert result.status == "completed"
    dry = [
        step
        for step in result.steps
        if step.kind == "tool_result" and step.output.get("dry_run") is True
    ]
    assert dry and dry[0].output["op"] == "snapshot"


# --- find_guide + show_guide ------------------------------------------------


async def test_find_guide_tool_returns_matching_tours(actx):
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(
        actx, 'How do I invoice? [[tool:find_guide {"query": "create an invoice"}]]'
    )
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    result_step = next(step for step in await get_steps(run_id) if step.kind == "tool_result")
    guides = result_step.output["guides"]
    assert [guide["id"] for guide in guides] == [tour_id]
    assert guides[0]["name"] == "Create an invoice"


async def test_find_guide_prefers_the_guide_for_the_page_the_visitor_is_on(actx):
    await make_tour(actx.workspace_id, name="Invoices anywhere")
    scoped = await make_tour(actx.workspace_id, name="Invoices here", url_pattern="*/billing*")
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(
        actx, 'invoices? [[tool:find_guide {"query": "invoices"}]]'
    )
    await allow_page_control(conversation_id, url="https://app.test/billing")
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    result_step = next(step for step in await get_steps(run_id) if step.kind == "tool_result")
    assert result_step.output["guides"][0]["id"] == scoped


async def test_show_guide_is_pushed_to_the_page(actx):
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, f'Show me [[tool:show_guide {{"tour_id": "{tour_id}"}}]]'
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    run = await get_run(run_id)
    assert run.status == "awaiting_client"
    request = next(step for step in await get_steps(run_id) if step.kind == "client_request")
    assert request.output["op"] == "guide"
    assert request.output["args"]["tour_id"] == tour_id


# --- prompt -----------------------------------------------------------------


async def test_the_prompt_only_promises_guidance_when_the_tools_are_there(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, _ = await conversation_with_message(actx, "hi")
    async with session_scope() as session:
        agent = await session.get(Agent, agent_id)
        conversation = await session.get(Conversation, conversation_id)
        bare = engine.compose_system_prompt(agent, "Acme", conversation=conversation, plan=None)
        plan = await engine.tool_registry.resolve_agent_tools(
            session, actx.workspace_id, agent, conversation=conversation
        )
        with_tools = engine.compose_system_prompt(
            agent, "Acme", conversation=conversation, plan=plan
        )
    assert "find_guide" not in bare
    assert "find_guide" in with_tools
    assert "show_steps" in with_tools


async def test_the_prompt_states_where_the_visitor_is(actx):
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, _ = await conversation_with_message(actx, "hi")
    await allow_page_control(conversation_id, url="https://app.test/billing")
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        conversation.attributes = {**conversation.attributes, "page_title": "Billing"}
        agent = await session.get(Agent, agent_id)
        plan = await engine.tool_registry.resolve_agent_tools(
            session, actx.workspace_id, agent, conversation=conversation
        )
        prompt = engine.compose_system_prompt(agent, "Acme", conversation=conversation, plan=plan)
    assert 'currently on the page "Billing" (https://app.test/billing)' in prompt


async def test_a_show_only_agent_is_told_it_cannot_click(actx):
    agent_id = await make_agent(actx, settings=SHOW_ONLY)
    conversation_id, _ = await conversation_with_message(actx, "hi")
    await allow_page_control(conversation_id)
    async with session_scope() as session:
        agent = await session.get(Agent, agent_id)
        conversation = await session.get(Conversation, conversation_id)
        plan = await engine.tool_registry.resolve_agent_tools(
            session, actx.workspace_id, agent, conversation=conversation
        )
        prompt = engine.compose_system_prompt(agent, "Acme", conversation=conversation, plan=plan)
    assert "cannot click or type for" in prompt
    assert "do it FOR them" not in prompt


async def test_a_second_message_queues_behind_the_run_waiting_on_the_page(actx):
    """A visitor who keeps typing mid-guide must not get two agents on the page —
    and must not have the second message silently dropped either. It queues a
    follow-up run that only executes once the parked one reaches a terminal
    status."""
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, "Look [[tool:page_snapshot {}]]"
    )
    await allow_page_control(conversation_id)
    first_run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    assert (await get_run(first_run_id)).status == "awaiting_client"

    # The engine's message trigger only fires for an AI-owned conversation.
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        conversation.ai_agent_id = agent_id
        await session.commit()
    await add_contact_message(actx, conversation_id, "actually never mind")
    await drain_tasks()

    async with session_scope() as session:
        runs = (
            (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.conversation_id == conversation_id)
                    .order_by(AgentRun.created_at, AgentRun.id)
                )
            )
            .scalars()
            .all()
        )
        statuses = [run.status for run in runs]
    assert statuses == ["awaiting_client", "queued"], (
        "the parked run must not be joined by a concurrent run, and the new "
        "message must not be dropped"
    )

    # Once the parked run finishes, the follow-up executes (it is no longer
    # queued). The mock provider re-reads the page_snapshot directive from the
    # rebuilt history, so the follow-up parks on the page in turn — what
    # matters here is that the chain ran it at all.
    await resume_with(first_run_id, {"ok": True, "url": "https://app.test/", "elements": "[]"})
    await drain_tasks()
    async with session_scope() as session:
        runs = (
            (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.conversation_id == conversation_id)
                    .order_by(AgentRun.created_at, AgentRun.id)
                )
            )
            .scalars()
            .all()
        )
        statuses = [run.status for run in runs]
    assert statuses[0] == "completed"
    assert statuses[1] != "queued", "the follow-up must execute once the parked run finished"
