"""Widget endpoints for the in-app assistant.

The agent loop runs on the server; its page tools run in the visitor's browser.
That split needs exactly three endpoints:

``POST /conversations/{id}/page-context``   where the visitor is + consent
``GET  /conversations/{id}/copilot/pending`` is a page op waiting for me?
``POST /conversations/{id}/copilot/result``  here is the result, resume the run

Everything is scoped by the widget contact token, and every conversation is
re-checked against the caller's contact — a visitor must never be able to answer
another visitor's op or read where they are.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import engine, page_tools
from app.api.widget.deps import WidgetAuth, WidgetPrincipal
from app.core.deps import Db
from app.core.errors import ConflictError, NotFoundError
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.schemas.copilot import (
    ClientOpAck,
    ClientOpResultIn,
    PageContextIn,
    PageContextOut,
    PendingOpOut,
)

router = APIRouter()

#: Cap on a single page-op result. A serialized listing is budgeted client-side;
#: this is the backstop against a hostile or broken widget posting megabytes into
#: a tool result (and from there into the model's context).
MAX_RESULT_CHARS = 24_000


async def _owned_conversation(
    session: AsyncSession, principal: WidgetPrincipal, conversation_id: str
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if (
        conversation is None
        or conversation.workspace_id != principal.workspace.id
        or conversation.contact_id != principal.contact.id
    ):
        raise NotFoundError("Conversation not found")
    return conversation


async def _agent_of(session: AsyncSession, conversation: Conversation) -> Agent | None:
    if not conversation.ai_agent_id:
        return None
    agent = await session.get(Agent, conversation.ai_agent_id)
    if agent is None or agent.workspace_id != conversation.workspace_id:
        return None
    return agent


def _truncate(value: Any, budget: int) -> Any:
    """Cap the strings inside a page-op result, preserving its shape."""
    if isinstance(value, str):
        return value[:budget]
    if isinstance(value, list):
        return [_truncate(item, budget) for item in value[:60]]
    if isinstance(value, dict):
        return {str(key): _truncate(item, budget) for key, item in list(value.items())[:40]}
    return value


@router.post("/conversations/{conversation_id}/page-context", response_model=PageContextOut)
async def set_page_context(
    conversation_id: str, body: PageContextIn, principal: WidgetAuth, session: Db
) -> PageContextOut:
    """Record the visitor's current page and (optionally) their consent.

    Stored on `conversation.attributes` rather than in memory so the next agent
    run — possibly in another worker, after a reload — sees the same context and
    the same consent decision.
    """
    conversation = await _owned_conversation(session, principal, conversation_id)
    attributes = dict(conversation.attributes or {})
    attributes["page_url"] = body.url
    if body.title is not None:
        attributes["page_title"] = body.title
    if body.path is not None:
        attributes["page_path"] = body.path
    if body.allow_actions is not None:
        attributes["page_control_consent"] = body.allow_actions
    conversation.attributes = attributes
    await session.flush()

    agent = await _agent_of(session, conversation)
    offer, mutating = page_tools.client_tools_available(
        agent.settings if agent is not None else None, attributes
    )
    return PageContextOut(ok=True, page_control=offer, allow_actions=mutating)


@router.get("/conversations/{conversation_id}/copilot/pending", response_model=PendingOpOut | None)
async def pending_op(
    conversation_id: str, principal: WidgetAuth, session: Db
) -> PendingOpOut | None:
    """The page op this conversation is waiting on, if any.

    Polled once when the widget (re)connects: the realtime push is lost across a
    reload, and re-fetching beats leaving the person with a silent thread until
    the sweep times the run out.
    """
    conversation = await _owned_conversation(session, principal, conversation_id)
    run = (
        await session.execute(
            select(AgentRun)
            .where(
                AgentRun.conversation_id == conversation.id,
                AgentRun.workspace_id == conversation.workspace_id,
                AgentRun.status == "awaiting_client",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None:
        return None
    pending = run.pending_tool_call or {}
    op_id = pending.get("client_op_id")
    if not op_id or "result" in pending:
        return None
    return PendingOpOut(
        run_id=run.id,
        op_id=str(op_id),
        tool=str(pending.get("name") or ""),
        op=str(pending.get("op") or ""),
        args=page_tools.op_for(str(pending.get("name") or ""), pending.get("input") or {}).get(
            "args", {}
        ),
    )


@router.post("/conversations/{conversation_id}/copilot/result", response_model=ClientOpAck)
async def submit_op_result(
    conversation_id: str, body: ClientOpResultIn, principal: WidgetAuth, session: Db
) -> ClientOpAck:
    """Hand a page-op result back to the parked run so it can continue."""
    conversation = await _owned_conversation(session, principal, conversation_id)
    run = await session.get(AgentRun, body.run_id)
    if (
        run is None
        or run.conversation_id != conversation.id
        or run.workspace_id != conversation.workspace_id
    ):
        raise NotFoundError("Run not found")
    result = _truncate(body.result, MAX_RESULT_CHARS)
    try:
        await engine.submit_client_result(
            session, run, op_id=body.op_id, result=result if isinstance(result, dict) else {}
        )
    except ConflictError:
        # A stale widget replaying an old op (reconnect, double-submit). Not an
        # error worth surfacing to a visitor — the run has already moved on.
        return ClientOpAck(ok=True, status="ignored")
    return ClientOpAck(ok=True, status="resumed")
