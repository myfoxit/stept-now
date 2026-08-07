"""Widget public API: the visitor's own conversations.

Every endpoint is scoped to the authenticated contact (``widget_auth``). Only
``public`` messages are ever exposed — private notes and activity timeline
entries are stripped so nothing internal leaks to the visitor.

Inbound visitor messages reuse the authenticated principal's contact and
per-inbox identity directly (rather than routing through
``conversations.ingest_inbound``, which re-resolves the contact from
``contact_info`` and would mint a fresh contact for anonymous visitors, whose
``external_id``/``email`` are null). The conversation-reuse + inbound-message
path is otherwise identical to ``ingest_inbound``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.widget.deps import WidgetAuth, WidgetPrincipal
from app.core.db import utcnow
from app.core.deps import Db
from app.core.errors import NotFoundError
from app.core.events import Actor
from app.core.pagination import CursorPage
from app.core.ratelimit import RateLimit
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import AuthorType, Message, MessageDirection, MessageVisibility
from app.realtime.manager import broadcast, conversation_topic
from app.schemas.messages import AttachmentRef
from app.services import conversations as conversations_service

router = APIRouter()

PREVIEW_LENGTH = 140


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------


class ConversationSummary(BaseModel):
    id: str
    status: str
    last_message_preview: str | None = None
    last_activity_at: datetime
    unread: bool = False


class WidgetMessageOut(BaseModel):
    """Visitor-facing message: notes/activity are never serialized, and only the
    citations survive from ``meta`` (agent internals are stripped)."""

    id: str
    direction: str
    author_type: str
    author_name: str
    content: str
    attachments: list[dict[str, Any]]
    created_at: datetime
    meta: dict[str, Any]


class WidgetMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=150_000)
    attachments: list[AttachmentRef] = Field(default_factory=list)


class WidgetReplyCreate(BaseModel):
    message: str = Field(min_length=1, max_length=150_000)


class TypingRequest(BaseModel):
    is_typing: bool = True


# ---------------------------------------------------------------------------
# serialization helpers (shared with boot.py)
# ---------------------------------------------------------------------------


async def _last_public_preview(session: AsyncSession, conversation_id: str) -> str | None:
    content = (
        (
            await session.execute(
                select(Message.content)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.visibility == MessageVisibility.PUBLIC,
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return content[:PREVIEW_LENGTH] if content else None


async def _contact_unread(session: AsyncSession, conversation: Conversation) -> bool:
    """Public agent/AI replies the visitor hasn't seen yet (contact_last_seen_at)."""
    query = (
        select(func.count())
        .select_from(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.direction == MessageDirection.OUT,
            Message.visibility == MessageVisibility.PUBLIC,
        )
    )
    if conversation.contact_last_seen_at is not None:
        query = query.where(Message.created_at > conversation.contact_last_seen_at)
    return (await session.execute(query)).scalar_one() > 0


async def _summary(session: AsyncSession, conversation: Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        status=conversation.status,
        last_message_preview=await _last_public_preview(session, conversation.id),
        last_activity_at=conversation.last_activity_at,
        unread=await _contact_unread(session, conversation),
    )


async def conversation_summaries(
    session: AsyncSession,
    workspace_id: str,
    inbox_id: str,
    contact_id: str,
    *,
    limit: int = 10,
) -> list[ConversationSummary]:
    """The visitor's most-recent conversations on this widget inbox."""
    conversations = (
        (
            await session.execute(
                select(Conversation)
                .where(
                    Conversation.workspace_id == workspace_id,
                    Conversation.inbox_id == inbox_id,
                    Conversation.contact_id == contact_id,
                )
                .order_by(Conversation.last_activity_at.desc(), Conversation.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [await _summary(session, conversation) for conversation in conversations]


def _message_out(message: Message) -> WidgetMessageOut:
    citations = message.meta.get("citations") if isinstance(message.meta, dict) else None
    return WidgetMessageOut(
        id=message.id,
        direction=message.direction,
        author_type=message.author_type,
        author_name=message.author_name,
        content=message.content,
        attachments=list(message.attachments),
        created_at=message.created_at,
        meta={"citations": citations} if citations else {},
    )


async def _owned_conversation(
    session: AsyncSession, principal: WidgetPrincipal, conversation_id: str
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if (
        conversation is None
        or conversation.workspace_id != principal.workspace.id
        or conversation.contact_id != principal.contact.id
    ):
        raise NotFoundError("Conversation not found")
    return conversation


async def _ingest_visitor_message(
    session: AsyncSession,
    principal: WidgetPrincipal,
    *,
    content: str,
    attachments: list[dict[str, Any]] | None,
    force_new: bool = False,
) -> tuple[Conversation, Message]:
    actor = Actor(type="contact", id=principal.contact.id, label=principal.contact.name or None)
    conversation = None
    if not force_new:
        # Reuse the visitor's still-open thread (email/return-visit continuity).
        # "Send us a message" passes force_new=True so a deliberate new
        # conversation opens a fresh thread instead of appending to an old one,
        # matching what visitors expect from Intercom.
        conversation = (
            (
                await session.execute(
                    select(Conversation)
                    .where(
                        Conversation.contact_inbox_id == principal.contact_inbox.id,
                        Conversation.status != ConversationStatus.RESOLVED,
                    )
                    .order_by(Conversation.last_activity_at.desc(), Conversation.id.desc())
                )
            )
            .scalars()
            .first()
        )
    if conversation is None:
        conversation = await conversations_service.create_conversation(
            session,
            inbox=principal.inbox,
            contact=principal.contact,
            contact_inbox=principal.contact_inbox,
            actor=actor,
        )
    message = await conversations_service.add_message(
        session,
        conversation,
        direction=MessageDirection.IN.value,
        author_type=AuthorType.CONTACT.value,
        author_id=principal.contact.id,
        author_name=principal.contact.name or "Visitor",
        content=content,
        attachments=attachments,
        actor=actor,
        deliver=False,
    )
    return conversation, message


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(principal: WidgetAuth, session: Db) -> list[ConversationSummary]:
    return await conversation_summaries(
        session,
        principal.workspace.id,
        principal.inbox.id,
        principal.contact.id,
        limit=50,
    )


@router.post(
    "/conversations",
    response_model=ConversationSummary,
    status_code=201,
    # Opening a conversation can start a paid agent run — the cheapest thing
    # an unauthenticated visitor can do to us that costs real money.
    dependencies=[Depends(RateLimit("widget_conversation", times=10, seconds=60))],
)
async def create_conversation(
    body: WidgetMessageCreate, principal: WidgetAuth, session: Db
) -> ConversationSummary:
    conversation, _message = await _ingest_visitor_message(
        session,
        principal,
        content=body.message,
        attachments=[a.model_dump() for a in body.attachments],
        force_new=True,
    )
    return await _summary(session, conversation)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=CursorPage[WidgetMessageOut],
)
async def list_messages(
    conversation_id: str,
    principal: WidgetAuth,
    session: Db,
    cursor: str | None = None,
    limit: int | None = None,
) -> CursorPage[WidgetMessageOut]:
    """Public messages only — notes and activity entries are never returned.

    Filtering happens in the query (``public_only``), so every page is a full
    page of visitor-visible messages and the cursor never strands earlier ones.
    """
    conversation = await _owned_conversation(session, principal, conversation_id)
    messages, next_cursor = await conversations_service.list_messages(
        session, conversation, cursor=cursor, limit=limit, public_only=True
    )
    return CursorPage(items=[_message_out(m) for m in messages], next_cursor=next_cursor)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=WidgetMessageOut,
    status_code=201,
    dependencies=[Depends(RateLimit("widget_message", times=40, seconds=60))],
)
async def create_message(
    conversation_id: str, body: WidgetReplyCreate, principal: WidgetAuth, session: Db
) -> WidgetMessageOut:
    conversation = await _owned_conversation(session, principal, conversation_id)
    actor = Actor(type="contact", id=principal.contact.id, label=principal.contact.name or None)
    message = await conversations_service.add_message(
        session,
        conversation,
        direction=MessageDirection.IN.value,
        author_type=AuthorType.CONTACT.value,
        author_id=principal.contact.id,
        author_name=principal.contact.name or "Visitor",
        content=body.message,
        actor=actor,
        deliver=False,
    )
    return _message_out(message)


@router.post("/conversations/{conversation_id}/read", response_model=ConversationSummary)
async def mark_read(
    conversation_id: str, principal: WidgetAuth, session: Db
) -> ConversationSummary:
    conversation = await _owned_conversation(session, principal, conversation_id)
    conversation.contact_last_seen_at = utcnow()
    await session.flush()
    return await _summary(session, conversation)


@router.post("/conversations/{conversation_id}/typing", status_code=204)
async def typing(
    conversation_id: str, body: TypingRequest, principal: WidgetAuth, session: Db
) -> None:
    conversation = await _owned_conversation(session, principal, conversation_id)
    await broadcast(
        conversation_topic(conversation.id),
        "typing",
        {
            "conversation_id": conversation.id,
            "is_typing": body.is_typing,
            "source": "contact",
        },
    )
