"""Widget public API: the visitor's CSAT rating for their own conversation."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.widget.deps import WidgetAuth, WidgetPrincipal
from app.core.deps import Db
from app.core.errors import NotFoundError
from app.models.conversation import Conversation
from app.services import csat as csat_service

router = APIRouter()


class CsatRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    feedback: str | None = Field(default=None, max_length=5000)


class CsatOut(BaseModel):
    conversation_id: str
    rating: int
    feedback: str | None = None


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


@router.post("/conversations/{conversation_id}/csat", response_model=CsatOut)
async def submit_csat(
    conversation_id: str, body: CsatRequest, principal: WidgetAuth, session: Db
) -> CsatOut:
    conversation = await _owned_conversation(session, principal, conversation_id)
    response = await csat_service.record_response(
        session,
        principal.workspace.id,
        conversation_id=conversation.id,
        contact_id=principal.contact.id,
        rating=body.rating,
        feedback=body.feedback,
    )
    return CsatOut(
        conversation_id=response.conversation_id,
        rating=response.rating,
        feedback=response.feedback,
    )
