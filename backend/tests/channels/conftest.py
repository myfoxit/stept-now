"""Channel-suite fixtures: committed workspaces + email/slack/telegram inboxes,
and helpers to build conversations/messages for outbound-sender tests."""

from __future__ import annotations

from typing import Any

from app.core.db import get_session_factory, uuid7
from app.core.events import Actor
from app.models.contact import Contact
from app.models.inbox import Inbox
from app.models.workspace import Workspace
from app.services import conversations as conversations_service
from app.services import inboxes as inboxes_service


async def make_workspace() -> str:
    async with get_session_factory()() as session:
        workspace = Workspace(name="Acme", slug=f"acme-{uuid7()[:12]}", settings={})
        session.add(workspace)
        await session.commit()
        return workspace.id


async def make_inbox(
    workspace_id: str,
    *,
    channel_type: str,
    config: dict[str, Any] | None = None,
    secrets: dict[str, Any] | None = None,
    name: str = "Inbox",
) -> str:
    async with get_session_factory()() as session:
        inbox = await inboxes_service.create_inbox(
            session,
            workspace_id,
            actor=Actor.system(),
            name=name,
            channel_type=channel_type,
            config=config or {},
            secrets=secrets,
        )
        await session.commit()
        return inbox.id


async def make_outbound_message(
    workspace_id: str,
    inbox_id: str,
    *,
    contact_email: str | None = None,
    contact_source_id: str | None = None,
    content: str = "Here is your answer",
    subject: str | None = None,
) -> tuple[str, str]:
    """Create a contact + conversation (+ optional per-channel identity) and an
    outbound message. Returns (conversation_id, message_id)."""
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        assert inbox is not None
        contact = Contact(workspace_id=workspace_id, name="Jane Doe", email=contact_email)
        session.add(contact)
        await session.flush()
        contact_inbox = None
        if contact_source_id is not None:
            from app.models.inbox import ContactInbox

            contact_inbox = ContactInbox(
                workspace_id=workspace_id,
                contact_id=contact.id,
                inbox_id=inbox_id,
                source_id=contact_source_id,
            )
            session.add(contact_inbox)
            await session.flush()
        conversation = await conversations_service.create_conversation(
            session,
            inbox=inbox,
            contact=contact,
            contact_inbox=contact_inbox,
            subject=subject,
            actor=Actor.system(),
        )
        message = await conversations_service.add_message(
            session,
            conversation,
            direction="out",
            author_type="user",
            author_id=None,
            author_name="Sam Support",
            content=content,
            actor=Actor.system(),
            deliver=False,
        )
        await session.commit()
        return conversation.id, message.id
