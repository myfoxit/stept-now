"""Demo seed for the conversations domain: 2 inboxes, 6 realistic conversations.

Runs from `python -m app.seed` (idempotent — skips when the workspace already
has conversations). Works standalone: creates demo contacts only when the
directory seeder hasn't run yet, applies tags only when they exist.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow, uuid7
from app.core.events import Actor
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationPriority, ConversationStatus
from app.models.inbox import ChannelType, ContactInbox, Inbox
from app.models.message import AuthorType, MessageDirection, MessageVisibility
from app.services import conversations as conversations_service
from app.services import inboxes as inboxes_service

DEMO_CONTACTS: list[dict[str, Any]] = [
    {
        "name": "Maya Chen",
        "email": "maya@acme.io",
        "attributes": {"plan": "pro", "company": "Acme"},
    },
    {
        "name": "Lars Nielsen",
        "email": "lars@nordics.dev",
        "attributes": {"plan": "free", "company": "Nordics"},
    },
    {
        "name": "Priya Patel",
        "email": "priya@globex.com",
        "attributes": {"plan": "enterprise", "company": "Globex"},
    },
    {
        "name": "Tom Ruiz",
        "email": "tom@initech.dev",
        "attributes": {"plan": "pro", "company": "Initech"},
    },
    {
        "name": "Aiko Tanaka",
        "email": "aiko@umbrella.jp",
        "attributes": {"plan": "enterprise", "company": "Umbrella"},
    },
    {
        "name": "Sara Kim",
        "email": "sara@hooli.xyz",
        "attributes": {"plan": "free", "company": "Hooli"},
    },
]


async def _ensure_contacts(session: AsyncSession, workspace_id: str) -> list[Contact]:
    contacts = list(
        (
            await session.execute(
                select(Contact)
                .where(Contact.workspace_id == workspace_id)
                .order_by(Contact.created_at)
            )
        ).scalars()
    )
    known_emails = {c.email for c in contacts}
    for spec in DEMO_CONTACTS:
        if len(contacts) >= len(DEMO_CONTACTS):
            break
        if spec["email"] in known_emails:
            continue
        contact = Contact(
            workspace_id=workspace_id,
            name=spec["name"],
            email=spec["email"],
            attributes=spec["attributes"],
            first_seen_at=utcnow(),
            last_seen_at=utcnow(),
        )
        session.add(contact)
        contacts.append(contact)
    await session.flush()
    return contacts


async def _ensure_api_inbox(session: AsyncSession, workspace_id: str) -> Inbox:
    inbox = (
        (
            await session.execute(
                select(Inbox).where(
                    Inbox.workspace_id == workspace_id,
                    Inbox.channel_type == ChannelType.API,
                )
            )
        )
        .scalars()
        .first()
    )
    if inbox is not None:
        return inbox
    inbox = Inbox(
        workspace_id=workspace_id,
        name="Product API",
        channel_type=ChannelType.API,
        config={},
    )
    session.add(inbox)
    await session.flush()
    return inbox


async def _tag_ids_by_name(session: AsyncSession, workspace_id: str) -> dict[str, str]:
    """Tags belong to the directory agent — apply them only when present."""
    try:
        from app.models.tag import Tag
    except ImportError:
        return {}
    rows = await session.execute(select(Tag.name, Tag.id).where(Tag.workspace_id == workspace_id))
    return {name: tag_id for name, tag_id in rows.all()}


async def _widget_identity(
    session: AsyncSession, workspace_id: str, inbox: Inbox, contact: Contact
) -> ContactInbox:
    contact_inbox = ContactInbox(
        workspace_id=workspace_id,
        contact_id=contact.id,
        inbox_id=inbox.id,
        source_id=uuid7(),
    )
    session.add(contact_inbox)
    await session.flush()
    return contact_inbox


async def seed(session: AsyncSession, ctx: Any) -> None:
    workspace_id = ctx.workspace.id
    existing = (
        await session.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.workspace_id == workspace_id)
        )
    ).scalar_one()
    if existing:
        return

    widget_inbox = await inboxes_service.ensure_default_widget_inbox(session, workspace_id)
    api_inbox = await _ensure_api_inbox(session, workspace_id)
    contacts = await _ensure_contacts(session, workspace_id)
    tags = await _tag_ids_by_name(session, workspace_id)

    owner_actor = Actor(type="user", id=ctx.owner.id, label=ctx.owner.name)
    agent_actor = Actor(type="user", id=ctx.agent.id, label=ctx.agent.name)

    async def start(
        inbox: Inbox,
        contact: Contact,
        *,
        subject: str | None,
        assignee: str | None,
        priority: str = ConversationPriority.NONE.value,
    ) -> Conversation:
        contact_inbox = None
        if inbox.channel_type == ChannelType.WIDGET:
            contact_inbox = await _widget_identity(session, workspace_id, inbox, contact)
        conversation = await conversations_service.create_conversation(
            session,
            inbox=inbox,
            contact=contact,
            contact_inbox=contact_inbox,
            subject=subject,
            actor=Actor(type="contact", id=contact.id, label=contact.name),
        )
        conversation.assignee_user_id = assignee  # deterministic demo assignment
        conversation.priority = priority
        await session.flush()
        return conversation

    async def say(
        conversation: Conversation,
        contact: Contact,
        text: str,
        *,
        attachments: list | None = None,
    ) -> None:
        await conversations_service.add_message(
            session,
            conversation,
            direction=MessageDirection.IN.value,
            author_type=AuthorType.CONTACT.value,
            author_id=contact.id,
            author_name=contact.name,
            content=text,
            attachments=attachments,
            actor=Actor(type="contact", id=contact.id, label=contact.name),
        )

    async def reply(
        conversation: Conversation, actor: Actor, text: str, *, visibility: str = "public"
    ) -> None:
        await conversations_service.add_message(
            session,
            conversation,
            direction=MessageDirection.OUT.value,
            author_type=AuthorType.USER.value,
            author_id=actor.id,
            author_name=actor.label or "Teammate",
            content=text,
            visibility=visibility,
            actor=actor,
        )

    # 1 — urgent billing issue, assigned to the demo agent, tagged "billing".
    c1 = await start(
        widget_inbox,
        contacts[0],
        subject="Charged twice for the Pro plan",
        assignee=ctx.agent.id,
        priority=ConversationPriority.URGENT.value,
    )
    await say(c1, contacts[0], "Hi — we were charged twice for the Pro plan this month.")
    await reply(c1, agent_actor, "Sorry about that! Checking your invoices right now.")
    await say(c1, contacts[0], "Thanks. The duplicate charge is on invoice #4211.")
    await reply(
        c1,
        agent_actor,
        "Stripe shows a duplicate capture on the 3rd — refunding, ETA 3-5 business days.",
        visibility=MessageVisibility.NOTE.value,
    )
    if "billing" in tags:
        await conversations_service.add_tag(session, c1, tags["billing"], actor=agent_actor)

    # 2 — widget install question, unassigned.
    c2 = await start(widget_inbox, contacts[1], subject="Widget won't show up", assignee=None)
    await say(
        c2,
        contacts[1],
        "I pasted the embed snippet before </body> but the launcher never appears.",
    )
    await reply(
        c2,
        owner_actor,
        "Could you check the browser console? A CSP blocking script-src is the usual culprit.",
    )

    # 3 — AI-owned conversation sitting in "pending".
    c3 = await start(widget_inbox, contacts[2], subject="Export contacts to CSV", assignee=None)
    await say(c3, contacts[2], "How do I export my contacts to CSV?")
    await conversations_service.add_message(
        session,
        c3,
        direction=MessageDirection.OUT.value,
        author_type=AuthorType.AGENT.value,
        author_id=None,
        author_name="Stept AI",
        content="You can export contacts from Directory → Contacts → Export. "
        "Want me to walk you through it?",
        actor=Actor(type="agent", label="Stept AI"),
    )
    await conversations_service.update_status(
        session, c3, ConversationStatus.PENDING.value, actor=Actor.system()
    )

    # 4 — snoozed feature request via the API inbox.
    c4 = await start(
        api_inbox,
        contacts[3],
        subject="Feature request: dark-mode widget",
        assignee=None,
        priority=ConversationPriority.LOW.value,
    )
    await say(c4, contacts[3], "Any plans for a dark-mode variant of the chat widget?")
    await reply(c4, owner_actor, "It's on the roadmap for next quarter — snoozing until then.")
    await conversations_service.update_status(
        session,
        c4,
        ConversationStatus.SNOOZED.value,
        actor=owner_actor,
        snoozed_until=utcnow() + timedelta(days=7),
    )

    # 5 — resolved conversation, CSAT requested, tagged "vip".
    c5 = await start(widget_inbox, contacts[4], subject="SSO setup for Umbrella", assignee=None)
    await say(c5, contacts[4], "We need SAML SSO configured for our team of 40.")
    await reply(c5, agent_actor, "Happy to help — sending our SSO setup guide.")
    await say(c5, contacts[4], "That worked, everyone can log in now. Thanks!")
    await reply(c5, agent_actor, "Great! Closing this one — ping us anytime.")
    await conversations_service.update_status(
        session, c5, ConversationStatus.RESOLVED.value, actor=agent_actor
    )
    c5.csat_requested = True
    if "vip" in tags:
        await conversations_service.add_tag(session, c5, tags["vip"], actor=agent_actor)

    # 6 — open bug report via API inbox, high priority, with an internal note.
    c6 = await start(
        api_inbox,
        contacts[5],
        subject="Webhook retries firing twice",
        assignee=None,
        priority=ConversationPriority.HIGH.value,
    )
    await say(c6, contacts[5], "Our endpoint receives every webhook event twice since Monday.")
    await reply(
        c6,
        owner_actor,
        "Looks like a dedupe regression on our side — engineering is on it.",
        visibility=MessageVisibility.NOTE.value,
    )
    await say(c6, contacts[5], "Any update? It's polluting our job queue.")
    if "bug" in tags:
        await conversations_service.add_tag(session, c6, tags["bug"], actor=owner_actor)

    await session.flush()
