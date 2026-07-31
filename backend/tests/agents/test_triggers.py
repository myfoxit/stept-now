"""Event-driven trigger wiring: @on handlers register at import, a conversation on
an AI inbox goes pending + auto-runs on the first contact message, and human
takeover stops further runs."""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import session_scope, uuid7
from app.core.events import Actor, EventNames, _subscribers
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.message import Message
from app.services import conversations as conversations_service
from tests.agents.conftest import (
    add_contact_message,
    get_conversation,
    make_agent,
    new_conversation,
)
from tests.conftest import drain_tasks


async def make_wired_inbox(actx, agent_id: str) -> str:
    async with session_scope() as session:
        inbox = Inbox(
            workspace_id=actx.workspace_id,
            name="AI Widget",
            channel_type="widget",
            config={"ai_agent_id": agent_id},
            widget_key=f"wk_{uuid7()}",
        )
        session.add(inbox)
        await session.commit()
        return inbox.id


async def runs_for(conversation_id: str) -> list[AgentRun]:
    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(AgentRun).where(AgentRun.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )


async def activity_texts(conversation_id: str) -> list[str]:
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(Message.content).where(
                        Message.conversation_id == conversation_id,
                        Message.visibility == "activity",
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


def test_engine_handlers_registered_at_import():
    # Importing the router package (done at app build) registers the @on handlers.
    import app.api.v1.agents  # noqa: F401

    assert len(_subscribers.get(EventNames.CONVERSATION_CREATED, [])) >= 1
    assert len(_subscribers.get(EventNames.MESSAGE_CREATED, [])) >= 1


async def test_conversation_created_on_ai_inbox_goes_pending(actx):
    agent_id = await make_agent(actx, name="Sage")
    inbox_id = await make_wired_inbox(actx, agent_id)
    conversation_id = await new_conversation(actx, inbox_id=inbox_id)

    conversation = await get_conversation(conversation_id)
    assert conversation.status == "pending"
    assert conversation.ai_agent_id == agent_id
    assert any("joined the conversation" in text for text in await activity_texts(conversation_id))


async def test_first_contact_message_triggers_run(actx):
    agent_id = await make_agent(actx)
    inbox_id = await make_wired_inbox(actx, agent_id)
    conversation_id = await new_conversation(actx, inbox_id=inbox_id)

    await add_contact_message(actx, conversation_id, "How do I get started?")
    await drain_tasks()

    runs = await runs_for(conversation_id)
    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert runs[0].trigger_message_id is not None

    # The agent posted a public reply.
    async with session_scope() as session:
        replies = (
            (
                await session.execute(
                    select(Message).where(
                        Message.conversation_id == conversation_id,
                        Message.author_type == "agent",
                        Message.visibility == "public",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert replies


async def test_human_takeover_stops_further_runs(actx):
    agent_id = await make_agent(actx)
    inbox_id = await make_wired_inbox(actx, agent_id)
    conversation_id = await new_conversation(actx, inbox_id=inbox_id)

    # A teammate takes over → status open.
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        await conversations_service.update_status(
            session,
            conversation,
            "open",
            actor=Actor(type="user", id=None, label="Teammate"),
        )
        await session.commit()

    await add_contact_message(actx, conversation_id, "another question")
    await drain_tasks()

    assert await runs_for(conversation_id) == []  # no run while a human owns it


async def test_no_duplicate_run_while_one_is_active(actx):
    agent_id = await make_agent(actx)  # close_conversation → require_approval
    inbox_id = await make_wired_inbox(actx, agent_id)
    conversation_id = await new_conversation(actx, inbox_id=inbox_id)

    await add_contact_message(
        actx, conversation_id, '[[tool:close_conversation {"closing_message": "bye"}]]'
    )
    await drain_tasks()
    runs = await runs_for(conversation_id)
    assert len(runs) == 1 and runs[0].status == "awaiting_approval"

    # A second message arrives while the run is parked — no new run is spawned.
    await add_contact_message(actx, conversation_id, "are you there?")
    await drain_tasks()
    assert len(await runs_for(conversation_id)) == 1
