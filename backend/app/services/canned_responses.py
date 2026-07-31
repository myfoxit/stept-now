"""Canned response CRUD (saved replies with {{placeholders}})."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.events import Actor
from app.models.canned_response import CannedResponse
from app.services import audit


async def get_response(
    session: AsyncSession, workspace_id: str, response_id: str
) -> CannedResponse:
    response = await session.get(CannedResponse, response_id)
    if response is None or response.workspace_id != workspace_id:
        raise NotFoundError("Canned response not found")
    return response


async def list_responses(session: AsyncSession, workspace_id: str) -> list[CannedResponse]:
    result = await session.execute(
        select(CannedResponse)
        .where(CannedResponse.workspace_id == workspace_id)
        .order_by(CannedResponse.shortcut)
    )
    return list(result.scalars())


async def create_response(
    session: AsyncSession, workspace_id: str, *, actor: Actor, shortcut: str, content: str
) -> CannedResponse:
    response = CannedResponse(
        workspace_id=workspace_id,
        shortcut=shortcut.strip(),
        content=content,
        created_by=actor.id if actor.type == "user" else None,
    )
    session.add(response)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("A canned response with this shortcut already exists") from exc
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="canned_response.create",
        target_type="canned_response",
        target_id=response.id,
        meta={"shortcut": response.shortcut},
    )
    return response


async def update_response(
    session: AsyncSession,
    workspace_id: str,
    response_id: str,
    *,
    actor: Actor,
    shortcut: str | None = None,
    content: str | None = None,
) -> CannedResponse:
    response = await get_response(session, workspace_id, response_id)
    if shortcut is not None:
        response.shortcut = shortcut.strip()
    if content is not None:
        response.content = content
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("A canned response with this shortcut already exists") from exc
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="canned_response.update",
        target_type="canned_response",
        target_id=response.id,
        meta={"shortcut": response.shortcut},
    )
    return response


async def delete_response(
    session: AsyncSession, workspace_id: str, response_id: str, *, actor: Actor
) -> None:
    response = await get_response(session, workspace_id, response_id)
    await session.delete(response)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="canned_response.delete",
        target_type="canned_response",
        target_id=response_id,
        meta={"shortcut": response.shortcut},
    )
