"""conversations_seed: content shape + idempotency."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.message import Message
from app.seed import SeedContext
from app.services import conversations_seed
from tests.conversations.conftest import make_member, make_user, make_workspace


async def _build_ctx(session: AsyncSession) -> SeedContext:
    workspace = await make_workspace(session, "Seed Demo")
    owner = await make_user(session, "Odette Owner")
    agent = await make_user(session, "Sam Support")
    await make_member(session, workspace, owner, role="owner")
    await make_member(session, workspace, agent, role="agent")
    return SeedContext(workspace=workspace, owner=owner, agent=agent)


async def _conversations(session: AsyncSession, workspace_id: str) -> list[Conversation]:
    return list(
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.workspace_id == workspace_id)
                .order_by(Conversation.number)
            )
        ).scalars()
    )


async def test_seed_creates_demo_conversations(db_only: AsyncSession):
    ctx = await _build_ctx(db_only)
    await conversations_seed.seed(db_only, ctx)

    conversations = await _conversations(db_only, ctx.workspace.id)
    assert len(conversations) == 6
    assert sorted(c.number for c in conversations) == [1, 2, 3, 4, 5, 6]

    statuses = {c.status for c in conversations}
    assert {"open", "pending", "snoozed", "resolved"} <= statuses
    assert any(c.priority == "urgent" for c in conversations)

    resolved = [c for c in conversations if c.status == "resolved"]
    assert len(resolved) == 1
    assert resolved[0].csat_requested is True
    assert resolved[0].resolved_at is not None

    snoozed = [c for c in conversations if c.status == "snoozed"]
    assert snoozed and snoozed[0].snoozed_until is not None

    assert any(c.assignee_user_id == ctx.agent.id for c in conversations)

    inboxes = (
        (
            await db_only.execute(
                select(Inbox.channel_type).where(Inbox.workspace_id == ctx.workspace.id)
            )
        )
        .scalars()
        .all()
    )
    assert set(inboxes) == {"widget", "api"}
    assert {c.inbox_id for c in conversations}.__len__() == 2  # spread over both inboxes

    for conversation in conversations:
        public_count = (
            await db_only.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.visibility.in_(["public", "note"]),
                )
            )
        ).scalar_one()
        assert 1 <= public_count <= 6

    notes = (
        await db_only.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.workspace_id == ctx.workspace.id,
                Message.visibility == "note",
            )
        )
    ).scalar_one()
    assert notes >= 2  # private notes are part of the demo threads


async def test_seed_is_idempotent(db_only: AsyncSession):
    ctx = await _build_ctx(db_only)
    await conversations_seed.seed(db_only, ctx)
    await conversations_seed.seed(db_only, ctx)  # second run is a no-op

    total = (
        await db_only.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.workspace_id == ctx.workspace.id)
        )
    ).scalar_one()
    assert total == 6
