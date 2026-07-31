"""Conversation domain services — the single write path for conversations/messages.

Tracker rules (docs/research/chatwoot.md):
- inbound public message: waiting_since set if blank; resolved/snoozed reopen to
  open ("pending" stays pending — the AI agent owns it).
- outbound public reply by user/agent: first_reply_at set once, waiting_since cleared.
- notes/activity: bump last_activity_at only.
Unread is computed from agent_last_seen_at vs inbound message timestamps —
timestamps, not counters.

`add_message` is THE entry point for creating messages; channel webhooks call
`ingest_inbound`; both are relied upon by the channel/automation/agent waves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.channels.registry  # noqa: F401 — registers the deliver_message task
from app.core.db import utcnow
from app.core.errors import NotFoundError, ValidationFailure
from app.core.events import Actor, Event, EventNames, emit
from app.core.pagination import clamp_limit, decode_cursor, encode_cursor
from app.core.queue import enqueue
from app.models.contact import Contact
from app.models.conversation import (
    Conversation,
    ConversationCounter,
    ConversationPriority,
    ConversationStatus,
    ConversationTag,
)
from app.models.inbox import ChannelType, ContactInbox, Inbox
from app.models.message import (
    AuthorType,
    DeliveryStatus,
    Message,
    MessageDirection,
    MessageVisibility,
)
from app.models.user import User
from app.models.workspace import Membership
from app.realtime.manager import broadcast, conversation_topic, workspace_topic
from app.schemas.conversations import (
    AssigneeRef,
    ContactDetail,
    ContactRef,
    ConversationCounts,
    ConversationListItem,
    ConversationOut,
    InboxRef,
)
from app.schemas.messages import MessageOut

# Channels delivered in-app (websocket/REST pull) — no background delivery task.
_LOCAL_CHANNELS = {ChannelType.WIDGET.value, ChannelType.API.value}

_ASSIGNABLE_ROLES = ("owner", "admin", "agent")

PREVIEW_LENGTH = 140


class _Unset:
    """Sentinel distinguishing 'not provided' from an explicit None."""

    def __repr__(self) -> str:  # pragma: no cover
        return "<UNSET>"


_UNSET = _Unset()
UNSET = _UNSET  # public alias for callers of assign()


# ---------------------------------------------------------------------------
# lookups & serialization
# ---------------------------------------------------------------------------


async def get_conversation(
    session: AsyncSession, workspace_id: str, conversation_id: str
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.workspace_id != workspace_id:
        raise NotFoundError("Conversation not found")
    return conversation


async def tag_ids_for(session: AsyncSession, conversation_id: str) -> list[str]:
    result = await session.execute(
        select(ConversationTag.tag_id)
        .where(ConversationTag.conversation_id == conversation_id)
        .order_by(ConversationTag.created_at)
    )
    return list(result.scalars())


async def unread_count(session: AsyncSession, conversation: Conversation) -> int:
    """Inbound public messages the agent side hasn't seen yet (timestamps, not counters)."""
    query = (
        select(func.count())
        .select_from(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.direction == MessageDirection.IN,
            Message.visibility == MessageVisibility.PUBLIC,
        )
    )
    if conversation.agent_last_seen_at is not None:
        query = query.where(Message.created_at > conversation.agent_last_seen_at)
    return (await session.execute(query)).scalar_one()


async def conversation_out(
    session: AsyncSession,
    conversation: Conversation,
    *,
    contact: Contact | None = None,
    inbox: Inbox | None = None,
) -> ConversationOut:
    """Full detail payload (also used by conversation.* realtime broadcasts)."""
    contact = contact or await session.get(Contact, conversation.contact_id)
    inbox = inbox or await session.get(Inbox, conversation.inbox_id)
    if contact is None or inbox is None:  # pragma: no cover — FKs guarantee both
        raise NotFoundError("Conversation not found")
    assignee: AssigneeRef | None = None
    if conversation.assignee_user_id is not None:
        user = await session.get(User, conversation.assignee_user_id)
        if user is not None:
            assignee = AssigneeRef(id=user.id, name=user.name)
    return ConversationOut(
        id=conversation.id,
        number=conversation.number,
        subject=conversation.subject,
        status=conversation.status,
        priority=conversation.priority,
        snoozed_until=conversation.snoozed_until,
        contact=ContactDetail(
            id=contact.id,
            name=contact.name,
            email=contact.email,
            avatar_url=contact.avatar_url,
            external_id=contact.external_id,
            phone=contact.phone,
            verified=contact.verified,
            attributes=dict(contact.attributes),
            last_seen_at=contact.last_seen_at,
            created_at=contact.created_at,
        ),
        inbox=InboxRef(id=inbox.id, name=inbox.name, channel_type=inbox.channel_type),
        assignee=assignee,
        team_id=conversation.team_id,
        ai_agent_id=conversation.ai_agent_id,
        attributes=dict(conversation.attributes),
        waiting_since=conversation.waiting_since,
        first_reply_at=conversation.first_reply_at,
        resolved_at=conversation.resolved_at,
        last_activity_at=conversation.last_activity_at,
        agent_last_seen_at=conversation.agent_last_seen_at,
        contact_last_seen_at=conversation.contact_last_seen_at,
        csat_requested=conversation.csat_requested,
        tag_ids=await tag_ids_for(session, conversation.id),
        unread_count=await unread_count(session, conversation),
        created_at=conversation.created_at,
    )


def _summary_dict(conversation: Conversation) -> dict[str, Any]:
    """Compact JSON-safe conversation summary attached to message broadcasts."""

    def iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    return {
        "id": conversation.id,
        "number": conversation.number,
        "subject": conversation.subject,
        "status": conversation.status,
        "priority": conversation.priority,
        "inbox_id": conversation.inbox_id,
        "contact_id": conversation.contact_id,
        "assignee_user_id": conversation.assignee_user_id,
        "team_id": conversation.team_id,
        "waiting_since": iso(conversation.waiting_since),
        "first_reply_at": iso(conversation.first_reply_at),
        "last_activity_at": iso(conversation.last_activity_at),
    }


async def _broadcast_conversation(
    session: AsyncSession, conversation: Conversation, type: str
) -> None:
    payload = (await conversation_out(session, conversation)).model_dump(mode="json")
    await broadcast(workspace_topic(conversation.workspace_id), type, payload)
    await broadcast(conversation_topic(conversation.id), type, payload)


# ---------------------------------------------------------------------------
# numbering & auto-assignment
# ---------------------------------------------------------------------------


async def _next_number(session: AsyncSession, workspace_id: str) -> int:
    """Per-workspace sequential display number (row-locked counter on Postgres)."""
    counter = await session.get(ConversationCounter, workspace_id, with_for_update=True)
    if counter is None:
        try:
            async with session.begin_nested():
                counter = ConversationCounter(workspace_id=workspace_id, value=0)
                session.add(counter)
        except IntegrityError:  # lost the insert race — lock the winner's row
            counter = await session.get(ConversationCounter, workspace_id, with_for_update=True)
            if counter is None:  # pragma: no cover
                raise
    counter.value += 1
    await session.flush()
    return counter.value


async def _round_robin_assignee(session: AsyncSession, workspace_id: str) -> str | None:
    """Fairness-window auto-assignment (Chatwoot's V2 policy, no Redis queue):
    least open/pending load among available members, ties to the oldest member —
    sequential creations therefore rotate round-robin."""
    memberships = (
        (
            await session.execute(
                select(Membership)
                .where(
                    Membership.workspace_id == workspace_id,
                    Membership.is_available.is_(True),
                    Membership.role.in_(_ASSIGNABLE_ROLES),
                )
                .order_by(Membership.created_at, Membership.id)
            )
        )
        .scalars()
        .all()
    )
    if not memberships:
        return None
    load_rows = await session.execute(
        select(Conversation.assignee_user_id, func.count())
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.status.in_([ConversationStatus.OPEN, ConversationStatus.PENDING]),
            Conversation.assignee_user_id.is_not(None),
        )
        .group_by(Conversation.assignee_user_id)
    )
    load: dict[str, int] = {user_id: count for user_id, count in load_rows.all()}
    best = min(enumerate(memberships), key=lambda pair: (load.get(pair[1].user_id, 0), pair[0]))
    return best[1].user_id


# ---------------------------------------------------------------------------
# creation & ingestion
# ---------------------------------------------------------------------------


async def create_conversation(
    session: AsyncSession,
    *,
    inbox: Inbox,
    contact: Contact,
    contact_inbox: ContactInbox | None = None,
    subject: str | None = None,
    attributes: dict[str, Any] | None = None,
    actor: Actor,
) -> Conversation:
    now = utcnow()
    conversation = Conversation(
        workspace_id=inbox.workspace_id,
        number=await _next_number(session, inbox.workspace_id),
        inbox_id=inbox.id,
        contact_id=contact.id,
        contact_inbox_id=contact_inbox.id if contact_inbox is not None else None,
        subject=subject,
        attributes=attributes or {},
        waiting_since=now,  # cleared by the first human/agent reply
        last_activity_at=now,
    )
    if inbox.config.get("auto_assign"):
        conversation.assignee_user_id = await _round_robin_assignee(session, inbox.workspace_id)
    session.add(conversation)
    await session.flush()
    await emit(
        session,
        Event(
            name=EventNames.CONVERSATION_CREATED,
            workspace_id=conversation.workspace_id,
            payload={
                "conversation_id": conversation.id,
                "number": conversation.number,
                "inbox_id": inbox.id,
                "contact_id": contact.id,
            },
            actor=actor,
        ),
    )
    payload = (
        await conversation_out(session, conversation, contact=contact, inbox=inbox)
    ).model_dump(mode="json")
    await broadcast(workspace_topic(conversation.workspace_id), "conversation.created", payload)
    return conversation


async def _resolve_contact(
    session: AsyncSession, workspace_id: str, contact_info: dict[str, Any]
) -> Contact:
    """Find-or-create via the directory service; a minimal built-in fallback keeps
    ingestion working while that wave lands (same matching priority)."""
    kwargs: dict[str, Any] = {
        "external_id": contact_info.get("external_id"),
        "email": contact_info.get("email"),
        "name": contact_info.get("name"),
        "attributes": contact_info.get("attributes"),
        "verified": bool(contact_info.get("verified", False)),
    }
    try:
        from app.services.contacts import find_or_create  # directory agent's service
    except ImportError:
        find_or_create = None  # type: ignore[assignment]
    if find_or_create is not None:
        contact, _created = await find_or_create(session, workspace_id, **kwargs)
        return contact
    return await _find_or_create_contact_fallback(session, workspace_id, **kwargs)


async def _find_or_create_contact_fallback(
    session: AsyncSession,
    workspace_id: str,
    *,
    external_id: str | None,
    email: str | None,
    name: str | None,
    attributes: dict[str, Any] | None,
    verified: bool,
) -> Contact:
    contact: Contact | None = None
    if external_id:
        contact = (
            await session.execute(
                select(Contact).where(
                    Contact.workspace_id == workspace_id, Contact.external_id == external_id
                )
            )
        ).scalar_one_or_none()
    if contact is None and email:
        contact = (
            (
                await session.execute(
                    select(Contact)
                    .where(Contact.workspace_id == workspace_id, Contact.email == email)
                    .order_by(Contact.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
    now = utcnow()
    if contact is None:
        contact = Contact(
            workspace_id=workspace_id,
            external_id=external_id,
            email=email,
            name=name or "",
            attributes=attributes or {},
            verified=verified,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(contact)
        await session.flush()
        await emit(
            session,
            Event(
                name=EventNames.CONTACT_CREATED,
                workspace_id=workspace_id,
                payload={"contact_id": contact.id},
            ),
        )
        return contact
    if name:
        contact.name = name
    if email:
        contact.email = email
    if external_id and contact.external_id is None:
        contact.external_id = external_id
    if attributes:
        contact.attributes = {**contact.attributes, **attributes}
    if verified:
        contact.verified = True  # never downgraded
    contact.last_seen_at = now
    if contact.first_seen_at is None:
        contact.first_seen_at = now
    await session.flush()
    return contact


async def ingest_inbound(
    session: AsyncSession,
    inbox: Inbox,
    *,
    source_id: str,
    content: str,
    contact_info: dict,
    attachments: list | None = None,
    message_source_id: str | None = None,
    subject: str | None = None,
    meta: dict | None = None,
) -> tuple[Conversation, Message]:
    """Channel-agnostic inbound pipeline: contact → contact_inbox → conversation →
    message. Dedupes redeliveries by message_source_id within the inbox."""
    workspace_id = inbox.workspace_id

    if message_source_id:
        existing = (
            (
                await session.execute(
                    select(Message)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(
                        Conversation.inbox_id == inbox.id,
                        Message.source_id == message_source_id,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            conversation = await session.get(Conversation, existing.conversation_id)
            assert conversation is not None
            return conversation, existing

    contact = await _resolve_contact(session, workspace_id, contact_info or {})

    contact_inbox = (
        await session.execute(
            select(ContactInbox).where(
                ContactInbox.inbox_id == inbox.id, ContactInbox.source_id == source_id
            )
        )
    ).scalar_one_or_none()
    if contact_inbox is None:
        contact_inbox = ContactInbox(
            workspace_id=workspace_id,
            contact_id=contact.id,
            inbox_id=inbox.id,
            source_id=source_id,
            hmac_verified=bool((contact_info or {}).get("verified", False)),
        )
        session.add(contact_inbox)
        await session.flush()
    else:
        if contact_inbox.contact_id != contact.id:
            contact_inbox.contact_id = contact.id  # identity upgrade (e.g. email match)
        if (contact_info or {}).get("verified"):
            contact_inbox.hmac_verified = True

    actor = Actor(type="contact", id=contact.id, label=contact.name or None)
    conversation = (
        (
            await session.execute(
                select(Conversation)
                .where(
                    Conversation.contact_inbox_id == contact_inbox.id,
                    Conversation.status != ConversationStatus.RESOLVED,
                )
                .order_by(Conversation.last_activity_at.desc(), Conversation.id.desc())
            )
        )
        .scalars()
        .first()
    )
    if conversation is None:
        conversation = await create_conversation(
            session,
            inbox=inbox,
            contact=contact,
            contact_inbox=contact_inbox,
            subject=subject,
            actor=actor,
        )

    message = await add_message(
        session,
        conversation,
        direction=MessageDirection.IN,
        author_type=AuthorType.CONTACT,
        author_id=contact.id,
        author_name=contact.name or "Visitor",
        content=content,
        attachments=attachments,
        source_id=message_source_id,
        meta=meta,
        actor=actor,
        deliver=False,
    )
    return conversation, message


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------


async def add_message(
    session: AsyncSession,
    conversation: Conversation,
    *,
    direction: str,
    author_type: str,
    author_id: str | None,
    author_name: str,
    content: str,
    visibility: str = "public",
    attachments: list | None = None,
    source_id: str | None = None,
    meta: dict | None = None,
    actor: Actor,
    deliver: bool = True,
) -> Message:
    """THE single entry point for messages — updates trackers, emits, broadcasts,
    and schedules channel delivery for outbound public messages."""
    if direction not in MessageDirection:
        raise ValidationFailure(f"Unknown direction: {direction}")
    if visibility not in MessageVisibility:
        raise ValidationFailure(f"Unknown visibility: {visibility}")
    if author_type not in AuthorType:
        raise ValidationFailure(f"Unknown author type: {author_type}")

    if source_id:  # idempotent on channel redelivery
        existing = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id == conversation.id, Message.source_id == source_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    inbox = await session.get(Inbox, conversation.inbox_id)
    assert inbox is not None  # FK-guaranteed
    now = utcnow()

    is_public = visibility == MessageVisibility.PUBLIC
    is_outbound_reply = (
        is_public
        and direction == MessageDirection.OUT
        and author_type in (AuthorType.USER, AuthorType.AGENT)
    )
    wants_channel_delivery = (
        deliver
        and direction == MessageDirection.OUT
        and is_public
        and inbox.channel_type not in _LOCAL_CHANNELS
    )

    delivery_status: str | None = None
    if direction == MessageDirection.OUT and is_public:
        if inbox.channel_type in _LOCAL_CHANNELS:
            delivery_status = DeliveryStatus.SENT  # delivered in-app (websocket/REST)
        elif wants_channel_delivery:
            delivery_status = DeliveryStatus.PENDING

    message = Message(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        direction=direction,
        visibility=visibility,
        author_type=author_type,
        author_id=author_id,
        author_name=author_name,
        content=content,
        attachments=attachments or [],
        source_id=source_id,
        delivery_status=delivery_status,
        meta=meta or {},
    )
    session.add(message)

    # Tracker columns (see module docstring).
    previous_status = conversation.status
    if is_public and direction == MessageDirection.IN:
        if conversation.waiting_since is None:
            conversation.waiting_since = now
        if conversation.status in (ConversationStatus.RESOLVED, ConversationStatus.SNOOZED):
            # "pending" stays pending — the AI agent keeps ownership.
            conversation.status = ConversationStatus.OPEN
            conversation.snoozed_until = None
    elif is_outbound_reply:
        if conversation.first_reply_at is None:
            conversation.first_reply_at = now
        conversation.waiting_since = None
    conversation.last_activity_at = now
    await session.flush()

    if conversation.status != previous_status:
        await emit(
            session,
            Event(
                name=EventNames.CONVERSATION_STATUS_CHANGED,
                workspace_id=conversation.workspace_id,
                payload={
                    "conversation_id": conversation.id,
                    "status": conversation.status,
                    "previous_status": previous_status,
                },
                actor=actor,
            ),
        )
        await _broadcast_conversation(session, conversation, "conversation.updated")

    await emit(
        session,
        Event(
            name=EventNames.MESSAGE_CREATED,
            workspace_id=conversation.workspace_id,
            payload={
                "conversation_id": conversation.id,
                "message_id": message.id,
                "direction": direction,
                "author_type": author_type,
            },
            actor=actor,
        ),
    )
    data = {
        "message": MessageOut.model_validate(message).model_dump(mode="json"),
        "conversation": _summary_dict(conversation),
    }
    await broadcast(workspace_topic(conversation.workspace_id), "message.created", data)
    await broadcast(conversation_topic(conversation.id), "message.created", data)

    if wants_channel_delivery:
        await enqueue("deliver_message", message_id=message.id)
    return message


async def list_messages(
    session: AsyncSession,
    conversation: Conversation,
    *,
    cursor: str | None = None,
    limit: int | None = None,
) -> tuple[list[Message], str | None]:
    """Newest page first (cursor walks older); items within a page are returned
    in ascending order — natural chat display without client-side reversal."""
    page_size = clamp_limit(limit, default=30, maximum=100)
    query = (
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    if cursor:
        created_raw, row_id = decode_cursor(cursor, 2)
        created = datetime.fromisoformat(created_raw)
        query = query.where(
            or_(
                Message.created_at < created,
                (Message.created_at == created) & (Message.id < row_id),
            )
        )
    rows = list((await session.execute(query.limit(page_size + 1))).scalars())
    next_cursor = None
    if len(rows) > page_size:
        rows = rows[:page_size]
        next_cursor = encode_cursor(rows[-1].created_at.isoformat(), rows[-1].id)
    rows.reverse()
    return rows, next_cursor


# ---------------------------------------------------------------------------
# state transitions
# ---------------------------------------------------------------------------


async def _add_activity(
    session: AsyncSession, conversation: Conversation, content: str, *, actor: Actor
) -> None:
    author_type = actor.type if actor.type in AuthorType else AuthorType.SYSTEM.value
    await add_message(
        session,
        conversation,
        direction=MessageDirection.OUT,
        author_type=author_type,
        author_id=actor.id,
        author_name=actor.label or "System",
        content=content,
        visibility=MessageVisibility.ACTIVITY,
        actor=actor,
        deliver=False,
    )


async def update_status(
    session: AsyncSession,
    conversation: Conversation,
    status: str,
    *,
    actor: Actor,
    snoozed_until: datetime | None = None,
) -> Conversation:
    if status not in ConversationStatus:
        raise ValidationFailure(f"Unknown status: {status}")
    previous_status = conversation.status
    conversation.status = status
    conversation.snoozed_until = snoozed_until if status == ConversationStatus.SNOOZED else None
    if status == ConversationStatus.RESOLVED:
        conversation.resolved_at = utcnow()
        conversation.waiting_since = None  # resolve clears the needs-response queue
    await session.flush()
    if status != previous_status:
        label = actor.label or "System"
        verbs = {
            ConversationStatus.OPEN.value: "reopened the conversation"
            if previous_status in (ConversationStatus.RESOLVED, ConversationStatus.SNOOZED)
            else "opened the conversation",
            ConversationStatus.PENDING.value: "moved the conversation to the AI queue",
            ConversationStatus.SNOOZED.value: "snoozed the conversation",
            ConversationStatus.RESOLVED.value: "resolved the conversation",
        }
        await _add_activity(session, conversation, f"{label} {verbs[status]}", actor=actor)
        await emit(
            session,
            Event(
                name=EventNames.CONVERSATION_STATUS_CHANGED,
                workspace_id=conversation.workspace_id,
                payload={
                    "conversation_id": conversation.id,
                    "status": status,
                    "previous_status": previous_status,
                },
                actor=actor,
            ),
        )
    await _broadcast_conversation(session, conversation, "conversation.updated")
    return conversation


async def assign(
    session: AsyncSession,
    conversation: Conversation,
    *,
    assignee_user_id: str | None | _Unset = _UNSET,
    team_id: str | None | _Unset = _UNSET,
    actor: Actor,
) -> Conversation:
    assignee_changed = False
    team_changed = False
    assignee_name: str | None = None
    if not isinstance(assignee_user_id, _Unset) and assignee_user_id != (
        conversation.assignee_user_id
    ):
        if assignee_user_id is not None:
            member = (
                await session.execute(
                    select(Membership).where(
                        Membership.workspace_id == conversation.workspace_id,
                        Membership.user_id == assignee_user_id,
                    )
                )
            ).scalar_one_or_none()
            if member is None:
                raise ValidationFailure("Assignee is not a member of this workspace")
            assignee_name = member.user.name
        conversation.assignee_user_id = assignee_user_id
        assignee_changed = True
    if not isinstance(team_id, _Unset) and team_id != conversation.team_id:
        if team_id is not None:
            await _validate_team(session, conversation.workspace_id, team_id)
        conversation.team_id = team_id
        team_changed = True
    if not (assignee_changed or team_changed):
        return conversation
    await session.flush()
    label = actor.label or "System"
    if assignee_changed:
        text = (
            f"{label} assigned the conversation to {assignee_name}"
            if conversation.assignee_user_id is not None
            else f"{label} unassigned the conversation"
        )
        await _add_activity(session, conversation, text, actor=actor)
    await emit(
        session,
        Event(
            name=EventNames.CONVERSATION_ASSIGNED,
            workspace_id=conversation.workspace_id,
            payload={
                "conversation_id": conversation.id,
                "assignee_user_id": conversation.assignee_user_id,
                "team_id": conversation.team_id,
            },
            actor=actor,
        ),
    )
    await _broadcast_conversation(session, conversation, "conversation.updated")
    return conversation


async def _validate_team(session: AsyncSession, workspace_id: str, team_id: str) -> None:
    """Best-effort — the Team model belongs to the directory agent (no FK)."""
    try:
        from app.models.team import Team
    except ImportError:
        return
    team = await session.get(Team, team_id)
    if team is None or team.workspace_id != workspace_id:
        raise NotFoundError("Team not found")


async def set_priority(
    session: AsyncSession, conversation: Conversation, priority: str, *, actor: Actor
) -> Conversation:
    if priority not in ConversationPriority:
        raise ValidationFailure(f"Unknown priority: {priority}")
    if priority == conversation.priority:
        return conversation
    conversation.priority = priority
    await session.flush()
    await emit(
        session,
        Event(
            name=EventNames.CONVERSATION_UPDATED,
            workspace_id=conversation.workspace_id,
            payload={"conversation_id": conversation.id, "priority": priority},
            actor=actor,
        ),
    )
    await _broadcast_conversation(session, conversation, "conversation.updated")
    return conversation


async def add_tag(
    session: AsyncSession, conversation: Conversation, tag_id: str, *, actor: Actor
) -> list[str]:
    await _validate_tag(session, conversation.workspace_id, tag_id)
    existing = (
        await session.execute(
            select(ConversationTag).where(
                ConversationTag.conversation_id == conversation.id,
                ConversationTag.tag_id == tag_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            ConversationTag(
                workspace_id=conversation.workspace_id,
                conversation_id=conversation.id,
                tag_id=tag_id,
            )
        )
        await session.flush()
        await _tag_change_side_effects(session, conversation, actor)
    return await tag_ids_for(session, conversation.id)


async def remove_tag(
    session: AsyncSession, conversation: Conversation, tag_id: str, *, actor: Actor
) -> list[str]:
    existing = (
        await session.execute(
            select(ConversationTag).where(
                ConversationTag.conversation_id == conversation.id,
                ConversationTag.tag_id == tag_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()
        await _tag_change_side_effects(session, conversation, actor)
    return await tag_ids_for(session, conversation.id)


async def _validate_tag(session: AsyncSession, workspace_id: str, tag_id: str) -> None:
    """Best-effort — the Tag model belongs to the directory agent (no FK)."""
    try:
        from app.models.tag import Tag
    except ImportError:
        return
    tag = await session.get(Tag, tag_id)
    if tag is None or tag.workspace_id != workspace_id:
        raise NotFoundError("Tag not found")


async def _tag_change_side_effects(
    session: AsyncSession, conversation: Conversation, actor: Actor
) -> None:
    await emit(
        session,
        Event(
            name=EventNames.CONVERSATION_UPDATED,
            workspace_id=conversation.workspace_id,
            payload={
                "conversation_id": conversation.id,
                "tag_ids": await tag_ids_for(session, conversation.id),
            },
            actor=actor,
        ),
    )
    await _broadcast_conversation(session, conversation, "conversation.updated")


async def mark_read(session: AsyncSession, conversation: Conversation) -> Conversation:
    conversation.agent_last_seen_at = utcnow()
    await session.flush()
    return conversation


# ---------------------------------------------------------------------------
# listing & counts
# ---------------------------------------------------------------------------


def _apply_filters(
    query: Select[Any],
    workspace_id: str,
    *,
    status: list[str] | None,
    inbox_id: str | None,
    assignee: str | None,
    team_id: str | None,
    contact_id: str | None,
    tag_id: str | None,
    priority: str | None,
    q: str | None,
    current_user_id: str | None,
) -> Select[Any]:
    query = query.where(Conversation.workspace_id == workspace_id)
    if status:
        for value in status:
            if value not in ConversationStatus:
                raise ValidationFailure(f"Unknown status: {value}")
        query = query.where(Conversation.status.in_(status))
    if inbox_id:
        query = query.where(Conversation.inbox_id == inbox_id)
    if assignee:
        if assignee == "me":
            query = query.where(Conversation.assignee_user_id == current_user_id)
        elif assignee == "unassigned":
            query = query.where(Conversation.assignee_user_id.is_(None))
        else:
            query = query.where(Conversation.assignee_user_id == assignee)
    if team_id:
        query = query.where(Conversation.team_id == team_id)
    if contact_id:
        query = query.where(Conversation.contact_id == contact_id)
    if tag_id:
        query = query.where(
            select(ConversationTag.id)
            .where(
                ConversationTag.conversation_id == Conversation.id,
                ConversationTag.tag_id == tag_id,
            )
            .exists()
        )
    if priority:
        if priority not in ConversationPriority:
            raise ValidationFailure(f"Unknown priority: {priority}")
        query = query.where(Conversation.priority == priority)
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                Conversation.subject.ilike(pattern),
                Contact.name.ilike(pattern),
                Contact.email.ilike(pattern),
            )
        )
    return query


async def list_conversations(
    session: AsyncSession,
    workspace_id: str,
    *,
    status: list[str] | None = None,
    inbox_id: str | None = None,
    assignee: str | None = None,
    team_id: str | None = None,
    contact_id: str | None = None,
    tag_id: str | None = None,
    priority: str | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    current_user_id: str | None = None,
) -> tuple[list[ConversationListItem], str | None]:
    page_size = clamp_limit(limit, default=25, maximum=100)

    preview_sq = (
        select(Message.content)
        .where(
            Message.conversation_id == Conversation.id,
            Message.visibility == MessageVisibility.PUBLIC,
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )
    last_inbound_sq = (
        select(func.max(Message.created_at))
        .where(
            Message.conversation_id == Conversation.id,
            Message.direction == MessageDirection.IN,
            Message.visibility == MessageVisibility.PUBLIC,
        )
        .correlate(Conversation)
        .scalar_subquery()
    )

    query = (
        select(Conversation, Contact, Inbox, User, preview_sq, last_inbound_sq)
        .join(Contact, Contact.id == Conversation.contact_id)
        .join(Inbox, Inbox.id == Conversation.inbox_id)
        .outerjoin(User, User.id == Conversation.assignee_user_id)
    )
    query = _apply_filters(
        query,
        workspace_id,
        status=status,
        inbox_id=inbox_id,
        assignee=assignee,
        team_id=team_id,
        contact_id=contact_id,
        tag_id=tag_id,
        priority=priority,
        q=q,
        current_user_id=current_user_id,
    )
    query = query.order_by(Conversation.last_activity_at.desc(), Conversation.id.desc())
    if cursor:
        activity_raw, row_id = decode_cursor(cursor, 2)
        activity = datetime.fromisoformat(activity_raw)
        query = query.where(
            or_(
                Conversation.last_activity_at < activity,
                (Conversation.last_activity_at == activity) & (Conversation.id < row_id),
            )
        )

    rows = (await session.execute(query.limit(page_size + 1))).all()
    next_cursor = None
    if len(rows) > page_size:
        rows = rows[:page_size]
        last = rows[-1][0]
        next_cursor = encode_cursor(last.last_activity_at.isoformat(), last.id)

    conversation_ids = [row[0].id for row in rows]
    tags_by_conversation: dict[str, list[str]] = {}
    if conversation_ids:
        tag_rows = await session.execute(
            select(ConversationTag.conversation_id, ConversationTag.tag_id)
            .where(ConversationTag.conversation_id.in_(conversation_ids))
            .order_by(ConversationTag.created_at)
        )
        for conv_id, linked_tag_id in tag_rows.all():
            tags_by_conversation.setdefault(conv_id, []).append(linked_tag_id)

    items: list[ConversationListItem] = []
    for conversation, contact, inbox, assignee_user, preview, last_inbound_at in rows:
        unread = last_inbound_at is not None and (
            conversation.agent_last_seen_at is None
            or last_inbound_at > conversation.agent_last_seen_at
        )
        items.append(
            ConversationListItem(
                id=conversation.id,
                number=conversation.number,
                subject=conversation.subject,
                status=conversation.status,
                priority=conversation.priority,
                contact=ContactRef(
                    id=contact.id,
                    name=contact.name,
                    email=contact.email,
                    avatar_url=contact.avatar_url,
                ),
                inbox=InboxRef(id=inbox.id, name=inbox.name, channel_type=inbox.channel_type),
                assignee=(
                    AssigneeRef(id=assignee_user.id, name=assignee_user.name)
                    if assignee_user is not None
                    else None
                ),
                last_message_preview=(preview[:PREVIEW_LENGTH] if preview else None),
                last_activity_at=conversation.last_activity_at,
                unread=bool(unread),
                tag_ids=tags_by_conversation.get(conversation.id, []),
                waiting_since=conversation.waiting_since,
            )
        )
    return items, next_cursor


async def counts(
    session: AsyncSession, workspace_id: str, *, user_id: str | None = None
) -> ConversationCounts:
    status_rows = await session.execute(
        select(Conversation.status, func.count())
        .where(Conversation.workspace_id == workspace_id)
        .group_by(Conversation.status)
    )
    by_status: dict[str, int] = {row[0]: row[1] for row in status_rows.all()}
    open_scope = [
        Conversation.workspace_id == workspace_id,
        Conversation.status == ConversationStatus.OPEN,
    ]
    unassigned = (
        await session.execute(
            select(func.count())
            .select_from(Conversation)
            .where(*open_scope, Conversation.assignee_user_id.is_(None))
        )
    ).scalar_one()
    mine = 0
    if user_id is not None:
        mine = (
            await session.execute(
                select(func.count())
                .select_from(Conversation)
                .where(*open_scope, Conversation.assignee_user_id == user_id)
            )
        ).scalar_one()
    return ConversationCounts(
        open=by_status.get(ConversationStatus.OPEN.value, 0),
        unassigned=unassigned,
        mine=mine,
        pending=by_status.get(ConversationStatus.PENDING.value, 0),
        snoozed=by_status.get(ConversationStatus.SNOOZED.value, 0),
        resolved=by_status.get(ConversationStatus.RESOLVED.value, 0),
    )
