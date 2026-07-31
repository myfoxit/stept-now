"""Reports/search domain fixtures: builders that set explicit timestamps."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow, uuid7
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.csat import CsatResponse
from app.models.inbox import Inbox
from app.models.user import User
from app.models.workspace import Workspace


async def make_workspace(session: AsyncSession, name: str = "Reports WS") -> Workspace:
    workspace = Workspace(name=name, slug=f"ws-{uuid7()[:13]}", settings={})
    session.add(workspace)
    await session.flush()
    return workspace


async def make_inbox(
    session: AsyncSession, workspace: Workspace, *, channel_type: str = "widget"
) -> Inbox:
    inbox = Inbox(
        workspace_id=workspace.id,
        name=f"{channel_type} inbox",
        channel_type=channel_type,
        config={},
        widget_key=f"wk_{uuid7()}" if channel_type == "widget" else None,
    )
    session.add(inbox)
    await session.flush()
    return inbox


async def make_user(session: AsyncSession, name: str) -> User:
    user = User(email=f"user-{uuid7()}@example.com", name=name, password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def make_contact(session: AsyncSession, workspace: Workspace, name: str = "Nina") -> Contact:
    contact = Contact(workspace_id=workspace.id, name=name)
    session.add(contact)
    await session.flush()
    return contact


async def make_conversation(
    session: AsyncSession,
    workspace: Workspace,
    inbox: Inbox,
    contact: Contact,
    *,
    number: int,
    created_at: datetime,
    first_reply_at: datetime | None = None,
    resolved_at: datetime | None = None,
    assignee_user_id: str | None = None,
) -> Conversation:
    conversation = Conversation(
        workspace_id=workspace.id,
        number=number,
        inbox_id=inbox.id,
        contact_id=contact.id,
        status="resolved" if resolved_at else "open",
        created_at=created_at,
        last_activity_at=created_at,
        first_reply_at=first_reply_at,
        resolved_at=resolved_at,
        assignee_user_id=assignee_user_id,
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def make_csat(
    session: AsyncSession,
    workspace: Workspace,
    *,
    conversation_id: str,
    contact_id: str,
    rating: int,
    created_at: datetime | None = None,
) -> CsatResponse:
    csat = CsatResponse(
        workspace_id=workspace.id,
        conversation_id=conversation_id,
        contact_id=contact_id,
        rating=rating,
        created_at=created_at or utcnow(),
    )
    session.add(csat)
    await session.flush()
    return csat


async def build_six_conversations(session: AsyncSession, workspace: Workspace) -> dict[str, Any]:
    """Six conversations with known timestamps for deterministic report math."""
    now = utcnow()
    widget = await make_inbox(session, workspace, channel_type="widget")
    email = await make_inbox(session, workspace, channel_type="email")
    api = await make_inbox(session, workspace, channel_type="api")
    contact = await make_contact(session, workspace)
    alice = await make_user(session, "Alice")
    bob = await make_user(session, "Bob")

    def at(days_ago: int) -> datetime:
        return now - timedelta(days=days_ago)

    def plus(base: datetime, minutes: int) -> datetime:
        return base + timedelta(minutes=minutes)

    specs = [
        # (channel, days_ago, first_reply_min, resolution_min, assignee)
        (widget, 6, 10, 60, alice),
        (widget, 5, 20, 120, alice),
        (email, 4, 30, 180, bob),
        (email, 3, 40, None, bob),  # open (assigned but not resolved)
        (widget, 2, None, None, None),  # open, no first reply
        (api, 1, 50, 200, alice),
    ]
    conversations = []
    for i, (inbox, days_ago, fr, res, assignee) in enumerate(specs, start=1):
        created = at(days_ago)
        conversations.append(
            await make_conversation(
                session,
                workspace,
                inbox,
                contact,
                number=i,
                created_at=created,
                first_reply_at=plus(created, fr) if fr is not None else None,
                resolved_at=plus(created, res) if res is not None else None,
                assignee_user_id=assignee.id if assignee else None,
            )
        )

    await make_csat(
        session, workspace, conversation_id=conversations[0].id, contact_id=contact.id, rating=5
    )
    await make_csat(
        session, workspace, conversation_id=conversations[1].id, contact_id=contact.id, rating=4
    )

    return {"contact": contact, "alice": alice, "bob": bob, "conversations": conversations}
