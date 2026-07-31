"""The Sage seed: created live on the mock provider and wired onto the widget inbox."""

from __future__ import annotations

from sqlalchemy import func, select

from app.agents.seed import seed as agent_seed
from app.core.db import session_scope
from app.models.agent import Agent
from app.models.inbox import ChannelType, Inbox
from app.models.user import User
from app.models.workspace import Workspace
from app.seed import SeedContext


async def _run_seed(workspace_id: str) -> None:
    async with session_scope() as session:
        workspace = await session.get(Workspace, workspace_id)
        owner = (
            await session.execute(select(User).where(User.email == "owner@example.com"))
        ).scalar_one()
        await agent_seed(session, SeedContext(workspace=workspace, owner=owner, agent=owner))
        await session.commit()


async def test_seed_creates_sage_and_wires_widget_inbox(workspace_ctx):
    await _run_seed(workspace_ctx.id)

    async with session_scope() as session:
        sage = (
            await session.execute(
                select(Agent).where(Agent.workspace_id == workspace_ctx.id, Agent.name == "Sage")
            )
        ).scalar_one()
        assert sage.status == "live"
        assert sage.model_ref is None  # → workspace default → mock
        assert sage.settings["retrieval"]["enabled"] is True
        assert {"key": "close_conversation", "policy": "require_approval"} in sage.tools

        widget = (
            (
                await session.execute(
                    select(Inbox).where(
                        Inbox.workspace_id == workspace_ctx.id,
                        Inbox.channel_type == ChannelType.WIDGET,
                    )
                )
            )
            .scalars()
            .first()
        )
        assert widget is not None
        assert widget.config.get("ai_agent_id") == sage.id


async def test_seed_is_idempotent(workspace_ctx):
    await _run_seed(workspace_ctx.id)
    await _run_seed(workspace_ctx.id)
    async with session_scope() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(Agent)
                .where(Agent.workspace_id == workspace_ctx.id, Agent.name == "Sage")
            )
        ).scalar_one()
    assert count == 1
