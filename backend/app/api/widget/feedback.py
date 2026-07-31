"""Widget public API: the visitor's thumbs rating on answers in their conversation.

Contacts rate the answers they received — only outbound public messages are
ratable (never their own messages, never notes/activity).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.widget.deps import WidgetAuth
from app.core.deps import Db
from app.core.errors import NotFoundError, ValidationFailure
from app.models.conversation import Conversation
from app.models.message import Message, MessageDirection, MessageVisibility
from app.schemas.search_analytics import FeedbackCreate
from app.services import search_analytics as search_analytics_service

router = APIRouter()


@router.post("/conversations/{conversation_id}/messages/{message_id}/feedback")
async def submit_feedback(
    conversation_id: str,
    message_id: str,
    body: FeedbackCreate,
    principal: WidgetAuth,
    session: Db,
) -> dict[str, Any]:
    conversation = await session.get(Conversation, conversation_id)
    if (
        conversation is None
        or conversation.workspace_id != principal.workspace.id
        or conversation.contact_id != principal.contact.id
    ):
        raise NotFoundError("Conversation not found")
    message = await session.get(Message, message_id)
    if message is None or message.conversation_id != conversation.id:
        raise NotFoundError("Message not found")
    if message.direction != MessageDirection.OUT or message.visibility != MessageVisibility.PUBLIC:
        raise ValidationFailure("Only replies sent to you can be rated")

    feedback = await search_analytics_service.record_feedback(
        session,
        principal.workspace.id,
        conversation_id=conversation.id,
        message_id=message.id,
        rating=body.rating,
        comment=body.comment,
        actor_type="contact",
        actor_id=principal.contact.id,
    )
    return {"ok": True, "rating": feedback.rating}
