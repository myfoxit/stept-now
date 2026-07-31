"""Tag CRUD (shared by contact and conversation tagging)."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.events import Actor
from app.models.tag import ContactTag, Tag
from app.services import audit


async def get_tag(session: AsyncSession, workspace_id: str, tag_id: str) -> Tag:
    tag = await session.get(Tag, tag_id)
    if tag is None or tag.workspace_id != workspace_id:
        raise NotFoundError("Tag not found")
    return tag


async def list_tags(session: AsyncSession, workspace_id: str) -> list[Tag]:
    result = await session.execute(
        select(Tag).where(Tag.workspace_id == workspace_id).order_by(Tag.name)
    )
    return list(result.scalars())


async def create_tag(
    session: AsyncSession, workspace_id: str, *, actor: Actor, name: str, color: str
) -> Tag:
    tag = Tag(workspace_id=workspace_id, name=name.strip(), color=color)
    session.add(tag)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("A tag with this name already exists") from exc
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="tag.create",
        target_type="tag",
        target_id=tag.id,
        meta={"name": tag.name, "color": tag.color},
    )
    return tag


async def update_tag(
    session: AsyncSession,
    workspace_id: str,
    tag_id: str,
    *,
    actor: Actor,
    name: str | None = None,
    color: str | None = None,
) -> Tag:
    tag = await get_tag(session, workspace_id, tag_id)
    if name is not None:
        tag.name = name.strip()
    if color is not None:
        tag.color = color
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("A tag with this name already exists") from exc
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="tag.update",
        target_type="tag",
        target_id=tag.id,
        meta={"name": tag.name, "color": tag.color},
    )
    return tag


async def delete_tag(
    session: AsyncSession, workspace_id: str, tag_id: str, *, actor: Actor
) -> None:
    tag = await get_tag(session, workspace_id, tag_id)
    # Explicit link cleanup (SQLite doesn't enforce FK cascades by default).
    await session.execute(delete(ContactTag).where(ContactTag.tag_id == tag_id))
    await session.delete(tag)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="tag.delete",
        target_type="tag",
        target_id=tag_id,
        meta={"name": tag.name},
    )
