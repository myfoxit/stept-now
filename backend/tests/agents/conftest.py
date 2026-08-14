"""Fixtures + helpers for agent-engine tests.

`actx` builds on the root `workspace_ctx` (so tests get owner API headers and
`add_member`) plus a widget inbox + contact created directly in the DB. Most
engine tests drive `run_now` (create a queued run and execute it directly, which
is fully deterministic); approvals resume through the real API decide + queue.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select

from app.agents import engine
from app.core import events
from app.core.db import get_session_factory, session_scope, uuid7
from app.core.events import Actor
from app.models.agent import Agent
from app.models.agent_run import AgentRun, AgentStep, ApprovalRequest
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.user import User
from app.models.workspace import Workspace
from app.services import conversations as conversations_service

DEFAULT_SETTINGS: dict[str, Any] = {
    "retrieval": {"enabled": True, "k": 6, "source_ids": None},
    "handoff_message": "Let me connect you with a teammate.",
    "guardrails": {"max_tool_calls": 8, "require_citations": False},
}


@dataclass
class AgentsCtx:
    wc: Any  # WorkspaceCtx (root fixture): .id, .base, .owner_headers, add_member
    inbox_id: str
    contact_id: str

    @property
    def workspace_id(self) -> str:
        return self.wc.id

    @property
    def base(self) -> str:
        return self.wc.base

    @property
    def owner_headers(self) -> dict[str, str]:
        return self.wc.owner_headers


@pytest.fixture
async def actx(workspace_ctx) -> AgentsCtx:
    async with session_scope() as session:
        inbox = Inbox(
            workspace_id=workspace_ctx.id,
            name="Widget",
            channel_type="widget",
            config={},
            widget_key=f"wk_{uuid7()}",
        )
        session.add(inbox)
        contact = Contact(workspace_id=workspace_ctx.id, name="Casey Customer", email="casey@x.io")
        session.add(contact)
        await session.commit()
        return AgentsCtx(wc=workspace_ctx, inbox_id=inbox.id, contact_id=contact.id)


@contextlib.contextmanager
def capture_events(*names: str) -> Iterator[list[events.Event]]:
    """Record emitted domain events without disturbing app subscribers
    (same pattern as the integrations suite)."""
    captured: list[events.Event] = []

    async def _handler(session: Any, event: events.Event) -> None:
        captured.append(event)

    for name in names:
        events._subscribers.setdefault(name, []).append(_handler)
    try:
        yield captured
    finally:
        for name in names:
            events._subscribers[name].remove(_handler)


# --- builders (all commit so background/API sessions see them) ---------------


async def make_agent(
    actx: AgentsCtx,
    *,
    name: str | None = None,
    status: str = "live",
    tools: list[dict] | None = None,
    settings: dict | None = None,
    system_prompt: str = "",
    model_ref: str | None = "mock",
) -> str:
    async with session_scope() as session:
        agent = Agent(
            workspace_id=actx.workspace_id,
            name=name or f"Agent {uuid7()[:6]}",
            status=status,
            model_ref=model_ref,
            system_prompt=system_prompt,
            temperature=None,
            settings=settings or dict(DEFAULT_SETTINGS),
            tools=tools or [],
        )
        session.add(agent)
        await session.commit()
        return agent.id


async def new_conversation(actx: AgentsCtx, *, inbox_id: str | None = None) -> str:
    async with session_scope() as session:
        inbox = await session.get(Inbox, inbox_id or actx.inbox_id)
        contact = await session.get(Contact, actx.contact_id)
        assert inbox is not None and contact is not None
        conversation = await conversations_service.create_conversation(
            session,
            inbox=inbox,
            contact=contact,
            actor=Actor(type="contact", id=contact.id, label=contact.name),
        )
        await session.commit()
        return conversation.id


async def add_contact_message(actx: AgentsCtx, conversation_id: str, text: str) -> str:
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        message = await conversations_service.add_message(
            session,
            conversation,
            direction="in",
            author_type="contact",
            author_id=actx.contact_id,
            author_name="Casey Customer",
            content=text,
            actor=Actor(type="contact", id=actx.contact_id, label="Casey Customer"),
        )
        await session.commit()
        return message.id


async def conversation_with_message(actx: AgentsCtx, text: str) -> tuple[str, str]:
    conversation_id = await new_conversation(actx)
    message_id = await add_contact_message(actx, conversation_id, text)
    return conversation_id, message_id


async def run_now(
    actx: AgentsCtx, agent_id: str, conversation_id: str, *, trigger_message_id: str | None = None
) -> str:
    """Create a queued run and execute it directly (deterministic, no queue).

    A real run only fires on an AI-owned (``pending``) conversation, so mirror that.
    """
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
            trigger_message_id=trigger_message_id,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        await engine.execute_run(session, run)
        await session.commit()
    return run_id


# --- readers -----------------------------------------------------------------


async def get_run(run_id: str) -> AgentRun:
    async with session_scope() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        return run


async def get_steps(run_id: str) -> list[AgentStep]:
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.ord)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def step_kinds(run_id: str) -> list[str]:
    return [step.kind for step in await get_steps(run_id)]


async def get_conversation(conversation_id: str) -> Conversation:
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        return conversation


async def get_pending_approval(run_id: str) -> ApprovalRequest:
    async with session_scope() as session:
        approval = (
            await session.execute(select(ApprovalRequest).where(ApprovalRequest.run_id == run_id))
        ).scalar_one()
        return approval


async def public_messages(conversation_id: str) -> list[Any]:
    from app.models.message import Message

    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.visibility == "public",
                    )
                    .order_by(Message.created_at)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def get_user_id(email: str) -> str:
    async with session_scope() as session:
        return (await session.execute(select(User.id).where(User.email == email))).scalar_one()


async def approval_notified_user_ids(workspace_id: str) -> set[str]:
    from app.models.notification import Notification

    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(Notification.user_id).where(
                        Notification.workspace_id == workspace_id,
                        Notification.type == "approval",
                    )
                )
            )
            .scalars()
            .all()
        )
        return set(rows)


async def seed_rag_docs(workspace_id: str) -> None:
    from app.rag.seed import seed as rag_seed
    from app.seed import SeedContext

    async with get_session_factory()() as session:
        workspace = await session.get(Workspace, workspace_id)
        owner = (
            await session.execute(select(User).where(User.email == "owner@example.com"))
        ).scalar_one()
        await rag_seed(session, SeedContext(workspace=workspace, owner=owner, agent=owner))
        await session.commit()
