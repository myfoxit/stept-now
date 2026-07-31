"""SLA-suite fixtures/helpers.

Importing app.services.slas registers its @on(...) handlers (auto-apply +
finalize) for service-level (db_only) tests that fire events without the app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.slas  # noqa: F401 — registers event handlers + the scan job
from app.core.db import get_session_factory, uuid7
from app.core.events import Actor
from app.models.contact import Contact
from app.models.inbox import Inbox
from app.models.sla import SlaPolicy
from app.models.user import User
from app.models.workspace import Membership, Workspace

SYSTEM = Actor.system()


async def make_workspace(session: AsyncSession, name: str = "SLA WS") -> Workspace:
    workspace = Workspace(name=name, slug=f"ws-{uuid7()}", settings={})
    session.add(workspace)
    await session.flush()
    return workspace


async def make_inbox(
    session: AsyncSession,
    workspace: Workspace,
    *,
    channel_type: str = "widget",
    config: dict[str, Any] | None = None,
) -> Inbox:
    inbox = Inbox(
        workspace_id=workspace.id,
        name="Test inbox",
        channel_type=channel_type,
        config=config or {},
        widget_key=f"wk_{uuid7()}" if channel_type == "widget" else None,
    )
    session.add(inbox)
    await session.flush()
    return inbox


async def make_contact(
    session: AsyncSession, workspace: Workspace, *, name: str = "Nina Doe"
) -> Contact:
    contact = Contact(workspace_id=workspace.id, name=name)
    session.add(contact)
    await session.flush()
    return contact


async def make_user(session: AsyncSession, name: str = "Sam Support") -> User:
    user = User(email=f"user-{uuid7()}@example.com", name=name, password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def make_member(
    session: AsyncSession, workspace: Workspace, user: User, role: str = "agent"
) -> Membership:
    membership = Membership(workspace_id=workspace.id, user_id=user.id, role=role)
    session.add(membership)
    await session.flush()
    return membership


async def make_policy(
    session: AsyncSession,
    workspace: Workspace,
    *,
    name: str = "Gold",
    frt: int | None = None,
    nrt: int | None = None,
    rt: int | None = None,
) -> SlaPolicy:
    policy = SlaPolicy(
        workspace_id=workspace.id,
        name=name,
        first_response_minutes=frt,
        next_response_minutes=nrt,
        resolution_minutes=rt,
    )
    session.add(policy)
    await session.flush()
    return policy


@dataclass
class SlaCtx:
    session: AsyncSession
    workspace: Workspace
    inbox: Inbox
    contact: Contact
    user: User


@pytest.fixture
async def sla(db_only: AsyncSession) -> SlaCtx:
    workspace = await make_workspace(db_only)
    inbox = await make_inbox(db_only, workspace)
    contact = await make_contact(db_only, workspace)
    user = await make_user(db_only)
    await make_member(db_only, workspace, user)
    return SlaCtx(session=db_only, workspace=workspace, inbox=inbox, contact=contact, user=user)


# --- API-side helpers -------------------------------------------------------


async def create_contact_via_db(workspace_id: str, *, name: str = "Nina Doe") -> str:
    """Insert + commit a contact so API requests (separate sessions) see it."""
    async with get_session_factory()() as session:
        contact = Contact(workspace_id=workspace_id, name=name)
        session.add(contact)
        await session.commit()
        return contact.id


async def create_inbox_via_api(client, ctx, *, channel_type: str = "widget") -> dict:
    response = await client.post(
        f"{ctx.base}/inboxes",
        json={"name": f"{channel_type} inbox", "channel_type": channel_type, "config": {}},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_conversation_via_api(client, ctx, *, inbox_id: str, contact_id: str) -> dict:
    response = await client.post(
        f"{ctx.base}/conversations",
        json={"contact_id": contact_id, "inbox_id": inbox_id, "content": "Hello!"},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()
