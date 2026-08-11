"""1 inbound → exactly 1 reply, in order.

The dogfood defect: a message arriving while a run was active was silently
DROPPED — the visitor's English KB question was never answered; the reply that
came 13s later belonged to the previous message. These tests pin the new
routing: supersede (cancel-and-merge) while a run is still queued, sequential
follow-ups while one is in flight, and `meta.reply_to` on every reply.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.agents import engine
from app.core.db import session_scope, utcnow
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from tests.agents.conftest import (
    add_contact_message,
    conversation_with_message,
    get_conversation,
    get_run,
    make_agent,
    public_messages,
    run_now,
)
from tests.agents.test_page_tools import PAGE_CONTROL, allow_page_control, resume_with
from tests.conftest import drain_tasks


async def _runs(conversation_id: str) -> list[AgentRun]:
    async with session_scope() as session:
        return list(
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


async def _bind_agent(conversation_id: str, agent_id: str) -> None:
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        conversation.ai_agent_id = agent_id
        if conversation.status == "open":
            conversation.status = "pending"
        await session.commit()


async def _queued_run(actx, agent_id: str, conversation_id: str, message_id: str) -> str:
    """A run the trigger would have created, still queued (not yet executed)."""
    async with session_scope() as session:
        run = AgentRun(
            workspace_id=actx.workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            trigger_message_id=message_id,
            status="queued",
        )
        session.add(run)
        await session.commit()
        return run.id


async def test_reply_records_which_message_it_answers(actx):
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(actx, "What are your plans?")
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    assert (await get_run(run_id)).status == "completed"
    replies = [m for m in await public_messages(conversation_id) if m.author_type == "agent"]
    assert len(replies) == 1
    assert replies[0].meta.get("reply_to") == message_id


async def test_message_while_queued_cancels_and_merges(actx):
    """Two fast messages → ONE reply covering both (never two runs, never a
    dropped message). The superseded run is canceled, not executed."""
    agent_id = await make_agent(actx)
    conversation_id, first_id = await conversation_with_message(actx, "How do I export data?")
    await _bind_agent(conversation_id, agent_id)
    first_run_id = await _queued_run(actx, agent_id, conversation_id, first_id)

    second_id = await add_contact_message(actx, conversation_id, "…and can I schedule it?")
    await drain_tasks()

    runs = await _runs(conversation_id)
    assert [run.status for run in runs] == ["canceled", "completed"]
    assert runs[0].id == first_run_id
    assert "superseded" in (runs[0].error or "")
    assert runs[1].trigger_message_id == second_id

    replies = [m for m in await public_messages(conversation_id) if m.author_type == "agent"]
    assert len(replies) == 1, "cancel-and-merge means exactly one reply for both messages"
    assert replies[0].meta.get("reply_to") == second_id


async def test_sequential_follow_up_answers_each_message_once(actx):
    """A message during an in-flight run queues a follow-up; each message ends
    with its own reply, each stamped with the message it answers."""
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, first_id = await conversation_with_message(
        actx, "Look at my screen [[tool:page_snapshot {}]]"
    )
    await allow_page_control(conversation_id)
    first_run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=first_id)
    assert (await get_run(first_run_id)).status == "awaiting_client"

    await _bind_agent(conversation_id, agent_id)
    second_id = await add_contact_message(actx, conversation_id, "also, what plans do you offer?")
    await drain_tasks()
    runs = await _runs(conversation_id)
    assert [run.status for run in runs] == ["awaiting_client", "queued"]

    # First run finishes → the chain executes the follow-up. The mock provider
    # re-reads the snapshot directive from history, so the follow-up parks on
    # the page too; answer it and it completes.
    await resume_with(first_run_id, {"ok": True, "url": "https://app.test/", "elements": "[]"})
    runs = await _runs(conversation_id)
    assert runs[0].status == "completed"
    assert runs[1].status == "awaiting_client"
    await resume_with(runs[1].id, {"ok": True, "url": "https://app.test/", "elements": "[]"})

    runs = await _runs(conversation_id)
    assert [run.status for run in runs] == ["completed", "completed"]
    replies = [m for m in await public_messages(conversation_id) if m.author_type == "agent"]
    assert [m.meta.get("reply_to") for m in replies] == [first_id, second_id], (
        "each inbound message gets addressed exactly once, in order"
    )


async def test_follow_up_cancels_itself_after_human_takeover(actx):
    """If a human takes the thread while a follow-up waits, the AI stands down."""
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(actx, "hello")
    await _bind_agent(conversation_id, agent_id)
    run_id = await _queued_run(actx, agent_id, conversation_id, message_id)
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        conversation.status = "open"  # teammate took over before the run started
        await session.commit()

    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        await engine.execute_run(session, run)
        await session.commit()

    run = await get_run(run_id)
    assert run.status == "canceled"
    assert "left the AI queue" in (run.error or "")
    assert (await get_conversation(conversation_id)).status == "open"


# --- reaper: hard server-side timeouts ---------------------------------------


async def test_reaper_fails_a_run_whose_worker_died(actx):
    """status="running" with an expired lease must reach a TERMINAL status —
    the widget's "working on the page…" can then never outlive its run."""
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(actx, "hi")
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        conversation.status = "pending"
        run = AgentRun(
            workspace_id=actx.workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            trigger_message_id=message_id,
            status="running",
            started_at=utcnow() - timedelta(minutes=10),
        )
        session.add(run)
        await session.commit()
        run_id = run.id
    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        run.lease_expires_at = utcnow() - timedelta(minutes=5)
        await session.commit()

    async with session_scope() as session:
        reaped = await engine.reap_stalled_runs(session)
        await session.commit()
    assert run_id in [run.id for run in reaped]
    await drain_tasks()

    run = await get_run(run_id)
    assert run.status == "failed"
    assert (await get_conversation(conversation_id)).status == "open", (
        "the visitor is handed to a human, not stranded"
    )


async def test_reaper_reenqueues_a_stale_queued_run(actx):
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(actx, "are you there?")
    await _bind_agent(conversation_id, agent_id)
    run_id = await _queued_run(actx, agent_id, conversation_id, message_id)
    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        run.updated_at = utcnow() - timedelta(minutes=10)
        await session.commit()

    async with session_scope() as session:
        reaped = await engine.reap_stalled_runs(session)
        await session.commit()
    assert run_id in [run.id for run in reaped]
    await drain_tasks()
    assert (await get_run(run_id)).status == "completed"


async def test_fresh_runs_are_left_alone_by_the_reaper(actx):
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(actx, "hi")
    await _bind_agent(conversation_id, agent_id)
    await _queued_run(actx, agent_id, conversation_id, message_id)
    async with session_scope() as session:
        assert await engine.reap_stalled_runs(session) == []


async def test_terminal_status_is_published_on_the_conversation_topic(actx, monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    async def record(topic: str, event: str, payload: dict) -> None:
        calls.append((topic, event, payload))

    monkeypatch.setattr(engine, "broadcast", record)
    agent_id = await make_agent(actx)
    conversation_id, message_id = await conversation_with_message(actx, "hello there")
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    conv_updates = [
        payload
        for topic, event, payload in calls
        if topic == f"conv:{conversation_id}" and event == "agent_run.updated"
    ]
    assert conv_updates, "the widget's topic must hear about the run"
    assert conv_updates[-1]["status"] == "completed"
    assert conv_updates[-1]["terminal"] is True
    assert conv_updates[-1]["run_id"] == run_id
