"""Reply copilot: a retrieval-grounded draft suggestion for a human teammate.

Unlike the agent engine this never runs tools, never writes messages, and never
auto-sends — it does one retrieval over the customer's most recent messages and a
single generate with a drafting system prompt, returning a suggested reply plus
the sources it was grounded on.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.engine import apply_citations
from app.ai.base import ChatMessage, ChatRequest
from app.ai.registry import resolve_chat
from app.models.conversation import Conversation
from app.models.message import Message
from app.rag.context import build_context
from app.rag.retrieval import search_chunks
from app.services.search_analytics import record_search

_RECENT_CONTACT_MESSAGES = 3
_HISTORY_CAP = 20
#: Room for sources in a draft-reply prompt. Smaller than the agent's budget:
#: a human is going to read and edit this, so breadth beats depth.
_COPILOT_CONTEXT_TOKENS = 1200


async def suggest_reply(
    session: AsyncSession, conversation: Conversation, member_name: str
) -> dict[str, Any]:
    """Return {"content": <draft>, "citations": [...]} — never persisted, never sent."""
    recent_contact = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.direction == "in",
                    Message.visibility == "public",
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(_RECENT_CONTACT_MESSAGES)
            )
        )
        .scalars()
        .all()
    )
    query = " ".join(message.content for message in reversed(recent_contact)).strip()

    citations: list[dict[str, Any]] = []
    context_block = "No knowledge-base sources were found."
    if query:
        results = await search_chunks(
            session,
            conversation.workspace_id,
            query,
            k=5,
            history=[message.content for message in reversed(recent_contact) if message.content],
        )
        await record_search(
            session,
            conversation.workspace_id,
            query=query,
            source="copilot",
            results_count=len(results),
            top_score=results[0].score if results else None,
        )
        context = build_context(results, query, max_tokens=_COPILOT_CONTEXT_TOKENS)
        citations = context.citation_dicts()
        if context.context_text:
            context_block = f"Sources:\n{context.context_text}"

    system = (
        f"You are a support copilot drafting a reply on behalf of {member_name}. "
        "Write a concise, friendly, accurate reply to the customer's latest message. "
        "Ground your answer only in the sources below and cite them inline as [n]; "
        "if the sources do not cover the question, say so plainly.\n\n"
        f"{context_block}"
    )

    messages: list[ChatMessage] = [ChatMessage.system(system)]
    messages.extend(await _history(session, conversation))

    provider, model_key = await resolve_chat(session, conversation.workspace_id, None)
    result = await provider.generate(
        ChatRequest(model=model_key, messages=messages, temperature=0.3)
    )
    cleaned, _referenced = apply_citations(result.content or "", citations)
    return {"content": cleaned or (result.content or ""), "citations": citations}


async def _history(session: AsyncSession, conversation: Conversation) -> list[ChatMessage]:
    rows = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.visibility == "public",
                )
                .order_by(Message.created_at, Message.id)
            )
        )
        .scalars()
        .all()
    )
    history = [
        ChatMessage(
            role="user" if message.author_type == "contact" else "assistant",
            content=message.content,
        )
        for message in rows
    ]
    return history[-_HISTORY_CAP:]
