"""CSAT responses: idempotent per-conversation recording + per-contact history.

(Not in the original file list of the directory agent, but the contract's
service section pins `csat.record_response` as a cross-agent dependency.)
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailure
from app.core.events import Actor, Event, EventNames, emit
from app.models.contact import Contact
from app.models.csat import CsatResponse


async def record_response(
    session: AsyncSession,
    workspace_id: str,
    *,
    conversation_id: str,
    contact_id: str,
    rating: int,
    feedback: str | None = None,
) -> CsatResponse:
    """Record a CSAT rating for a conversation. Idempotent: a re-submit for the
    same conversation updates the existing row. Emits `csat.submitted`."""
    if not 1 <= rating <= 5:
        raise ValidationFailure("rating must be between 1 and 5")
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.workspace_id != workspace_id:
        raise NotFoundError("Contact not found")

    response = (
        await session.execute(
            select(CsatResponse).where(
                CsatResponse.workspace_id == workspace_id,
                CsatResponse.conversation_id == conversation_id,
            )
        )
    ).scalar_one_or_none()
    if response is None:
        response = CsatResponse(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            contact_id=contact_id,
            rating=rating,
            feedback=feedback,
        )
        session.add(response)
    else:
        response.contact_id = contact_id
        response.rating = rating
        response.feedback = feedback
    await session.flush()
    await emit(
        session,
        Event(
            name=EventNames.CSAT_SUBMITTED,
            workspace_id=workspace_id,
            payload={
                "conversation_id": conversation_id,
                "contact_id": contact_id,
                "rating": rating,
            },
            actor=Actor(type="contact", id=contact_id),
        ),
    )
    return response


async def list_for_contact(
    session: AsyncSession, workspace_id: str, contact_id: str
) -> list[CsatResponse]:
    result = await session.execute(
        select(CsatResponse)
        .where(CsatResponse.workspace_id == workspace_id, CsatResponse.contact_id == contact_id)
        .order_by(CsatResponse.created_at.desc(), CsatResponse.id.desc())
    )
    return list(result.scalars())
