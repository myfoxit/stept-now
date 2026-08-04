"""Demo seed for the agent engine: the "Sage" agent, wired as the widget agent.

Sage runs on the offline mock provider (``model_ref`` null → workspace default →
mock), retrieves from the seeded product docs, and gates ``close_conversation``
behind human approval. It is wired onto the demo widget inbox
(``config.ai_agent_id``) so the end-to-end AI-answer and approval flows work out
of the box. Idempotent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentStatus
from app.services import inboxes as inboxes_service

if TYPE_CHECKING:
    from app.seed import SeedContext

SAGE_NAME = "Sage"
SAGE_DESCRIPTION = "Answers common questions from the Stept docs; sensitive actions need approval."

SAGE_SYSTEM_PROMPT = (
    "You are Sage, the friendly AI support agent for Stept (an open-source "
    "Intercom + Fin alternative). Answer the customer's question using the Stept "
    "product documentation available through the search_knowledge tool, and cite "
    "every fact inline as [n]. If the knowledge base does not confidently cover "
    "the question, or the customer is frustrated or asks for a person, hand off to "
    "a human instead of guessing."
)

SAGE_SETTINGS = {
    "retrieval": {"enabled": True, "k": 6, "source_ids": None},
    "handoff_message": "Let me bring in a teammate who can help with this.",
    "guardrails": {"max_tool_calls": 8, "require_citations": False},
    # In-app guidance is on for the demo so the widget shows what it can do out of
    # the box; acting on the page additionally needs the visitor's own consent in
    # the conversation, so nothing is clicked without a person saying yes.
    "page_control": {"enabled": True, "allow_actions": True},
}

# All other tools use their DEFAULT_POLICIES; close stays behind approval explicitly.
SAGE_TOOLS = [{"key": "close_conversation", "policy": "require_approval"}]


async def seed(session: AsyncSession, ctx: SeedContext) -> None:
    workspace_id = ctx.workspace.id

    agent = (
        (
            await session.execute(
                select(Agent).where(Agent.workspace_id == workspace_id, Agent.name == SAGE_NAME)
            )
        )
        .scalars()
        .first()
    )
    if agent is None:
        agent = Agent(
            workspace_id=workspace_id,
            name=SAGE_NAME,
            description=SAGE_DESCRIPTION,
            avatar_emoji="🦉",
            status=AgentStatus.LIVE,
            model_ref=None,  # → workspace default chat model → mock
            system_prompt=SAGE_SYSTEM_PROMPT,
            temperature=0.3,
            settings=dict(SAGE_SETTINGS),
            tools=[dict(entry) for entry in SAGE_TOOLS],
        )
        session.add(agent)
        await session.flush()

    # Wire Sage as the demo widget inbox's AI agent (idempotent).
    widget_inbox = await inboxes_service.ensure_default_widget_inbox(session, workspace_id)
    if (widget_inbox.config or {}).get("ai_agent_id") != agent.id:
        widget_inbox.config = {**(widget_inbox.config or {}), "ai_agent_id": agent.id}
        await session.flush()
