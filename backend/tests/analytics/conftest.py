"""Search-analytics fixtures: builders that pin timestamps for deterministic math."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory, utcnow, uuid7
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.message import Message
from app.models.search_analytics import SearchQuery
from app.models.workspace import Workspace


async def make_workspace(session: AsyncSession, name: str = "Analytics WS") -> Workspace:
    workspace = Workspace(name=name, slug=f"ws-{uuid7()[:13]}", settings={})
    session.add(workspace)
    await session.flush()
    return workspace


async def add_query(
    session: AsyncSession,
    workspace_id: str,
    *,
    query: str,
    source: str = "playground",
    results_count: int = 3,
    top_score: float | None = None,
    latency_ms: int = 0,
    days_ago: float = 0,
) -> SearchQuery:
    row = SearchQuery(
        workspace_id=workspace_id,
        query=query,
        source=source,
        results_count=results_count,
        top_score=top_score,
        latency_ms=latency_ms,
        created_at=utcnow() - timedelta(days=days_ago),
    )
    session.add(row)
    await session.flush()
    return row


async def seed_conversation_message(
    workspace_id: str,
    *,
    number: int = 1,
    direction: str = "out",
    visibility: str = "public",
    content: str = "The refund window is 30 days.",
) -> tuple[str, str]:
    """Commit an inbox + contact + conversation + one message; return their ids."""
    async with get_session_factory()() as session:
        inbox = Inbox(
            workspace_id=workspace_id,
            name="Widget inbox",
            channel_type="widget",
            config={},
            widget_key=f"wk_{uuid7()}",
        )
        contact = Contact(workspace_id=workspace_id, name="Nina")
        session.add_all([inbox, contact])
        await session.flush()
        conversation = Conversation(
            workspace_id=workspace_id,
            number=number,
            inbox_id=inbox.id,
            contact_id=contact.id,
        )
        session.add(conversation)
        await session.flush()
        message = Message(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            direction=direction,
            visibility=visibility,
            author_type="agent",
            author_name="Fin",
            content=content,
        )
        session.add(message)
        await session.flush()
        ids = (conversation.id, message.id)
        await session.commit()
    return ids
