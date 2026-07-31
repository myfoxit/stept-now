"""Core engine loop: happy path + trace sequence, guardrails, and the
never-strand-a-conversation fallbacks."""

from __future__ import annotations

from app.agents import engine
from app.ai.base import ChatResult, Usage
from app.core.db import session_scope
from app.models.tag import Tag
from tests.agents.conftest import (
    conversation_with_message,
    get_conversation,
    get_run,
    get_steps,
    make_agent,
    public_messages,
    run_now,
    seed_rag_docs,
    step_kinds,
)


async def test_happy_path_search_and_cite(actx):
    await seed_rag_docs(actx.workspace_id)
    agent_id = await make_agent(actx, system_prompt="You are Sage.")
    conversation_id, message_id = await conversation_with_message(
        actx, 'How do I install the widget? [[tool:search_knowledge {"query": "install widget"}]]'
    )
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    run = await get_run(run_id)
    assert run.status == "completed"
    assert run.reply_message_id is not None
    assert run.citations, "search results should be stored on the run"

    # Trace sequence exactly as specified in the contract.
    assert await step_kinds(run_id) == [
        "llm_call",
        "tool_call",
        "tool_result",
        "llm_call",
        "final_reply",
    ]

    messages = await public_messages(conversation_id)
    reply = messages[-1]
    assert reply.author_type == "agent"
    assert "[1]" in reply.content
    assert reply.meta["citations"], "reply meta.citations must be non-empty"
    assert reply.meta["agent_run_id"] == run_id


async def test_token_usage_aggregated_on_run(actx):
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(actx, "Hi there")
    run_id = await run_now(actx, agent_id, conversation_id)
    run = await get_run(run_id)
    assert run.input_tokens > 0
    assert run.output_tokens > 0


async def test_plain_answer_without_tools(actx):
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(actx, "Just a plain question")
    run_id = await run_now(actx, agent_id, conversation_id)
    run = await get_run(run_id)
    assert run.status == "completed"
    assert await step_kinds(run_id) == ["llm_call", "final_reply"]


async def test_disabled_tool_returns_error_and_model_continues(actx):
    agent_id = await make_agent(actx, tools=[{"key": "note_to_team", "policy": "disabled"}])
    conversation_id, _ = await conversation_with_message(
        actx, 'Please note this [[tool:note_to_team {"text": "internal"}]]'
    )
    run_id = await run_now(actx, agent_id, conversation_id)

    steps = await get_steps(run_id)
    disabled = [
        s for s in steps if s.kind == "tool_result" and s.output.get("error") == "tool disabled"
    ]
    assert disabled, "a disabled tool call must yield a tool_result error"
    run = await get_run(run_id)
    assert run.status == "completed"  # model saw the error and produced a reply
    # The disabled tool must NOT have executed — no internal note was created.
    from sqlalchemy import select

    from app.models.message import Message

    async with session_scope() as session:
        notes = (
            (
                await session.execute(
                    select(Message).where(
                        Message.conversation_id == conversation_id, Message.visibility == "note"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert notes == []


async def test_guardrail_max_tool_calls_hands_off(actx):
    settings = {
        "retrieval": {"enabled": True, "k": 6, "source_ids": None},
        "handoff_message": "handing off",
        "guardrails": {"max_tool_calls": 2, "require_citations": False},
    }
    agent_id = await make_agent(actx, settings=settings)
    directives = " ".join(f'[[tool:search_knowledge {{"query": "q{i}"}}]]' for i in range(5))
    conversation_id, _ = await conversation_with_message(actx, f"help {directives}")
    run_id = await run_now(actx, agent_id, conversation_id)

    run = await get_run(run_id)
    assert run.status == "handed_off"
    kinds = await step_kinds(run_id)
    assert "guardrail" in kinds and "handoff" in kinds
    # Exactly max_tool_calls tool executions occurred.
    assert kinds.count("tool_call") == 2
    conversation = await get_conversation(conversation_id)
    assert conversation.status == "open"


async def test_handoff_tool_opens_conversation(actx):
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(
        actx, 'I want a person [[tool:handoff_to_human {"reason": "wants a human"}]]'
    )
    run_id = await run_now(actx, agent_id, conversation_id)

    run = await get_run(run_id)
    assert run.status == "handed_off"
    assert "handoff" in await step_kinds(run_id)
    conversation = await get_conversation(conversation_id)
    assert conversation.status == "open"


async def test_note_to_team_creates_internal_note(actx):
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(
        actx, 'FYI [[tool:note_to_team {"text": "customer is on the pro plan"}]]'
    )
    run_id = await run_now(actx, agent_id, conversation_id)
    assert (await get_run(run_id)).status == "completed"

    from app.models.message import Message

    async with session_scope() as session:
        from sqlalchemy import select

        notes = (
            (
                await session.execute(
                    select(Message).where(
                        Message.conversation_id == conversation_id, Message.visibility == "note"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert any("pro plan" in n.content for n in notes)


async def test_tag_conversation_known_and_unknown(actx):
    async with session_scope() as session:
        session.add(Tag(workspace_id=actx.workspace_id, name="vip", color="#f00"))
        await session.commit()
    agent_id = await make_agent(actx)

    conversation_id, _ = await conversation_with_message(
        actx, '[[tool:tag_conversation {"tag_name": "vip"}]]'
    )
    run_id = await run_now(actx, agent_id, conversation_id)
    assert (await get_run(run_id)).status == "completed"
    from app.services.conversations import tag_ids_for

    async with session_scope() as session:
        assert await tag_ids_for(session, conversation_id)  # a tag was applied

    conversation_id2, _ = await conversation_with_message(
        actx, '[[tool:tag_conversation {"tag_name": "nope"}]]'
    )
    run_id2 = await run_now(actx, agent_id, conversation_id2)
    steps = await get_steps(run_id2)
    assert any(s.output.get("error") == "unknown tag" for s in steps if s.kind == "tool_result")


async def test_collect_contact_details_updates_contact(actx):
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(
        actx, '[[tool:collect_contact_details {"email": "casey@new.example", "name": "Casey New"}]]'
    )
    await run_now(actx, agent_id, conversation_id)
    from app.models.contact import Contact

    async with session_scope() as session:
        contact = await session.get(Contact, actx.contact_id)
        assert contact is not None and contact.email == "casey@new.example"
        assert contact.name == "Casey New"


async def test_empty_reply_falls_back_to_handoff(actx, monkeypatch):
    class _EmptyProvider:
        async def generate(self, request):
            return ChatResult(content="   ", tool_calls=[], finish_reason="stop", usage=Usage(2, 0))

        def stream(self, request):  # pragma: no cover - unused
            raise NotImplementedError

    async def _resolve(session, workspace_id, model_ref=None):
        return _EmptyProvider(), "mock"

    monkeypatch.setattr(engine, "resolve_chat", _resolve)
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(actx, "hello?")
    run_id = await run_now(actx, agent_id, conversation_id)

    assert (await get_run(run_id)).status == "handed_off"
    assert (await get_conversation(conversation_id)).status == "open"


async def test_provider_failure_never_strands_conversation(actx, monkeypatch):
    async def _boom(session, workspace_id, model_ref=None):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(engine, "resolve_chat", _boom)
    agent_id = await make_agent(actx)
    conversation_id, _ = await conversation_with_message(actx, "help me")
    run_id = await run_now(actx, agent_id, conversation_id)

    run = await get_run(run_id)
    assert run.status == "failed"
    assert run.error and "provider exploded" in run.error
    kinds = await step_kinds(run_id)
    assert "error" in kinds and "handoff" in kinds

    conversation = await get_conversation(conversation_id)
    assert conversation.status == "open"
    activity = [
        m.content
        for m in await _activity_messages(conversation_id)
        if "waiting for a teammate" in m.content
    ]
    assert activity, "a handoff activity note must be recorded"


async def _activity_messages(conversation_id: str):
    from sqlalchemy import select

    from app.models.message import Message

    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(Message).where(
                        Message.conversation_id == conversation_id,
                        Message.visibility == "activity",
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)
