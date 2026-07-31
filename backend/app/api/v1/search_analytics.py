"""Search analytics API: knowledge query stats + message feedback (thumbs)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Db, Member, require_perm
from app.core.errors import NotFoundError, ValidationFailure
from app.core.permissions import Perm
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.search_analytics import MessageFeedback
from app.schemas.search_analytics import (
    FeedbackCreate,
    MessageFeedbackOut,
    SearchAnalyticsOverview,
)
from app.services import search_analytics as search_analytics_service

router = APIRouter()

_ALLOWED_DAYS = (7, 30, 90)


@router.get(
    "/knowledge/analytics",
    response_model=SearchAnalyticsOverview,
    dependencies=[Depends(require_perm(Perm.REPORTS_READ))],
)
async def knowledge_analytics(
    principal: Member, session: Db, days: int = Query(30)
) -> SearchAnalyticsOverview:
    if days not in _ALLOWED_DAYS:
        raise ValidationFailure(f"days must be one of {_ALLOWED_DAYS}")
    data = await search_analytics_service.analytics_overview(
        session, principal.workspace.id, days=days
    )
    return SearchAnalyticsOverview.model_validate(data)


async def _message_in_workspace(
    session: AsyncSession, workspace_id: str, conversation_id: str, message_id: str
) -> Message:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.workspace_id != workspace_id:
        raise NotFoundError("Conversation not found")
    message = await session.get(Message, message_id)
    if message is None or message.conversation_id != conversation.id:
        raise NotFoundError("Message not found")
    return message


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=MessageFeedbackOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_WRITE))],
)
async def submit_message_feedback(
    conversation_id: str,
    message_id: str,
    body: FeedbackCreate,
    principal: Member,
    session: Db,
) -> MessageFeedbackOut:
    message = await _message_in_workspace(
        session, principal.workspace.id, conversation_id, message_id
    )
    feedback = await search_analytics_service.record_feedback(
        session,
        principal.workspace.id,
        conversation_id=message.conversation_id,
        message_id=message.id,
        rating=body.rating,
        comment=body.comment,
        actor_type="user",
        actor_id=principal.actor_id,
    )
    return MessageFeedbackOut.model_validate(feedback)


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=list[MessageFeedbackOut],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_message_feedback(
    conversation_id: str, message_id: str, principal: Member, session: Db
) -> list[MessageFeedbackOut]:
    message = await _message_in_workspace(
        session, principal.workspace.id, conversation_id, message_id
    )
    rows = (
        await session.execute(
            select(MessageFeedback)
            .where(
                MessageFeedback.workspace_id == principal.workspace.id,
                MessageFeedback.message_id == message.id,
            )
            .order_by(MessageFeedback.created_at.asc(), MessageFeedback.id.asc())
        )
    ).scalars()
    return [MessageFeedbackOut.model_validate(row) for row in rows]
