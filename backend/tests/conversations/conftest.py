"""Conversation-domain fixtures.

Service-level tests use `svc` (direct rows, no HTTP); API tests use the root
`workspace_ctx` plus the helpers here. Contacts are created directly via the
model — this suite never depends on the directory agent's service layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory, uuid7
from app.core.events import Actor
from app.models.contact import Contact
from app.models.inbox import Inbox
from app.models.user import User
from app.models.workspace import Membership, Workspace

SYSTEM = Actor.system()


async def make_workspace(session: AsyncSession, name: str = "Test WS") -> Workspace:
    workspace = Workspace(name=name, slug=f"ws-{uuid7()[:13]}", settings={})
    session.add(workspace)
    await session.flush()
    return workspace


async def make_user(session: AsyncSession, name: str, email: str | None = None) -> User:
    user = User(
        email=email or f"user-{uuid7()}@example.com",
        name=name,
        password_hash="x",  # never verified in these tests
    )
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


async def make_inbox(
    session: AsyncSession,
    workspace: Workspace,
    *,
    channel_type: str = "widget",
    config: dict[str, Any] | None = None,
    name: str = "Test inbox",
) -> Inbox:
    inbox = Inbox(
        workspace_id=workspace.id,
        name=name,
        channel_type=channel_type,
        config=config or {},
        widget_key=f"wk_{uuid7()}" if channel_type == "widget" else None,
    )
    session.add(inbox)
    await session.flush()
    return inbox


async def make_contact(
    session: AsyncSession,
    workspace: Workspace,
    *,
    name: str = "Nina Doe",
    email: str | None = None,
    external_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Contact:
    contact = Contact(
        workspace_id=workspace.id,
        name=name,
        email=email,
        external_id=external_id,
        attributes=attributes or {},
    )
    session.add(contact)
    await session.flush()
    return contact


@dataclass
class SvcCtx:
    """Direct-DB context for service-level tests (no HTTP, no auto-assign)."""

    session: AsyncSession
    workspace: Workspace
    inbox: Inbox
    contact: Contact
    user: User


@pytest.fixture
async def svc(db_only: AsyncSession) -> SvcCtx:
    workspace = await make_workspace(db_only)
    inbox = await make_inbox(db_only, workspace)  # auto_assign off
    contact = await make_contact(db_only, workspace, email="nina@example.com")
    user = await make_user(db_only, "Sam Support")
    await make_member(db_only, workspace, user, role="agent")
    return SvcCtx(session=db_only, workspace=workspace, inbox=inbox, contact=contact, user=user)


async def tag_id_in_session(session: AsyncSession, workspace_id: str) -> str:
    """A tag id usable in this session: a real Tag row when the directory
    agent's model exists, otherwise a bare id (link table has no FK)."""
    try:
        from app.models.tag import Tag
    except ImportError:
        return uuid7()
    try:
        tag = Tag(workspace_id=workspace_id, name=f"tag-{uuid7()[:8]}", color="#22c55e")
        session.add(tag)
        await session.flush()
        return tag.id
    except Exception:  # pragma: no cover — partially-built Tag model mid-wave
        pytest.skip("Tag model exists but is not constructible yet")


async def committed_team_id(workspace_id: str) -> str:
    """A team id visible to API requests: real Team row when the model exists."""
    try:
        from app.models.team import Team
    except ImportError:
        return uuid7()
    try:
        async with get_session_factory()() as session:
            team = Team(workspace_id=workspace_id, name=f"team-{uuid7()[:8]}")
            session.add(team)
            await session.commit()
            return team.id
    except Exception:  # pragma: no cover — partially-built Team model mid-wave
        pytest.skip("Team model exists but is not constructible yet")


# --- API-side helpers -------------------------------------------------------


async def create_contact_via_db(
    workspace_id: str,
    *,
    name: str = "Nina Doe",
    email: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> str:
    """Insert + commit a contact so API requests (separate sessions) can see it."""
    async with get_session_factory()() as session:
        contact = Contact(
            workspace_id=workspace_id, name=name, email=email, attributes=attributes or {}
        )
        session.add(contact)
        await session.commit()
        return contact.id


async def default_inbox(client, ctx) -> dict:
    """The auto-created widget inbox of a workspace_ctx workspace."""
    response = await client.get(f"{ctx.base}/inboxes", headers=ctx.owner_headers)
    assert response.status_code == 200, response.text
    widgets = [i for i in response.json() if i["channel_type"] == "widget"]
    assert widgets, "default widget inbox missing"
    return widgets[0]


async def create_inbox_via_api(
    client,
    ctx,
    *,
    name: str = "Api inbox",
    channel_type: str = "api",
    config: dict[str, Any] | None = None,
) -> dict:
    response = await client.post(
        f"{ctx.base}/inboxes",
        json={"name": name, "channel_type": channel_type, "config": config or {}},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def start_conversation(
    client, ctx, *, contact_id: str, inbox_id: str, content: str = "Hello there", subject=None
) -> dict:
    response = await client.post(
        f"{ctx.base}/conversations",
        json={"contact_id": contact_id, "inbox_id": inbox_id, "content": content}
        | ({"subject": subject} if subject else {}),
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()
