"""Directory-domain test fixtures (service-layer workspace without the app)."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.models.user import User
from app.models.workspace import Membership, Workspace


@dataclass
class DirCtx:
    session: AsyncSession
    workspace: Workspace
    owner: User
    agent: User


@pytest.fixture
async def dir_ctx(db_only) -> DirCtx:
    """Workspace + two member users for pure service-layer tests."""
    owner = User(email="dir-owner@example.com", name="Dir Owner", password_hash="x")
    agent = User(email="dir-agent@example.com", name="Dir Agent", password_hash="x")
    workspace = Workspace(name="Directory WS", slug="directory-ws")
    db_only.add_all([owner, agent, workspace])
    await db_only.flush()
    db_only.add_all(
        [
            Membership(workspace_id=workspace.id, user_id=owner.id, role="owner"),
            Membership(workspace_id=workspace.id, user_id=agent.id, role="agent"),
        ]
    )
    await db_only.flush()
    return DirCtx(session=db_only, workspace=workspace, owner=owner, agent=agent)


@contextmanager
def capture_events(name: str):
    """Collect emitted domain events of one name for the duration of a block."""
    captured: list[events.Event] = []

    async def handler(session, event) -> None:
        captured.append(event)

    events._subscribers.setdefault(name, []).append(handler)
    try:
        yield captured
    finally:
        events._subscribers[name].remove(handler)
