"""Saved views: named conversation filters, personal or shared.

The query document is validated through `app.services.filters` on write, so a
view can never persist something that would 500 on read.

Visibility rules: a personal view is visible only to its creator; a shared view
is visible to everyone in the workspace. Editing or deleting someone else's
personal view is not possible; changing a shared view needs the same permission
that created it (enforced in the router).

See docs/CHATWOOT-BACKLOG.md §1.3.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationFailure
from app.core.events import Actor
from app.models.saved_view import SavedView, ViewKind, ViewVisibility
from app.services import audit, filters


def _validate(kind: str, query: dict[str, Any] | None) -> None:
    if kind not in ViewKind:
        raise ValidationFailure(f"Unknown view kind {kind!r}")
    if kind == ViewKind.CONVERSATION.value:
        filters.validate_conversation_filter(query)
    else:
        # Contact views reuse the segment filter list (flat AND semantics).
        from app.services import segments as segments_service

        conditions = (query or {}).get("conditions") or []
        segments_service.compile_filters(list(conditions))


async def get_view(
    session: AsyncSession, workspace_id: str, view_id: str, *, user_id: str | None
) -> SavedView:
    view = await session.get(SavedView, view_id)
    if view is None or view.workspace_id != workspace_id:
        raise NotFoundError("View not found")
    if view.visibility == ViewVisibility.PERSONAL.value and view.created_by != user_id:
        raise NotFoundError("View not found")  # don't leak that it exists
    return view


async def list_views(
    session: AsyncSession, workspace_id: str, *, user_id: str | None, kind: str | None = None
) -> list[SavedView]:
    query = select(SavedView).where(
        SavedView.workspace_id == workspace_id,
        or_(
            SavedView.visibility == ViewVisibility.SHARED.value,
            SavedView.created_by == user_id,
        ),
    )
    if kind is not None:
        query = query.where(SavedView.kind == kind)
    query = query.order_by(SavedView.ord, SavedView.name)
    return list((await session.execute(query)).scalars())


async def create_view(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    user_id: str | None,
    name: str,
    kind: str = ViewKind.CONVERSATION.value,
    visibility: str = ViewVisibility.PERSONAL.value,
    query: dict[str, Any] | None = None,
    icon: str | None = None,
    ord: int = 0,
) -> SavedView:
    if visibility not in ViewVisibility:
        raise ValidationFailure(f"Unknown visibility {visibility!r}")
    _validate(kind, query)
    view = SavedView(
        workspace_id=workspace_id,
        name=name.strip(),
        kind=kind,
        visibility=visibility,
        query=query or {},
        icon=icon,
        ord=ord,
        created_by=user_id,
    )
    session.add(view)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="saved_view.create",
        target_type="saved_view",
        target_id=view.id,
        meta={"name": view.name, "visibility": visibility},
    )
    return view


async def update_view(
    session: AsyncSession,
    workspace_id: str,
    view_id: str,
    *,
    actor: Actor,
    user_id: str | None,
    changes: dict[str, Any],
) -> SavedView:
    view = await get_view(session, workspace_id, view_id, user_id=user_id)
    if view.visibility == ViewVisibility.PERSONAL.value and view.created_by != user_id:
        raise ForbiddenError("Cannot edit someone else's personal view")
    if "visibility" in changes and changes["visibility"] not in ViewVisibility:
        raise ValidationFailure(f"Unknown visibility {changes['visibility']!r}")
    if "query" in changes:
        _validate(str(changes.get("kind", view.kind)), changes["query"])
    for field in ("name", "visibility", "query", "icon", "ord"):
        if field in changes and changes[field] is not None:
            value = changes[field]
            setattr(view, field, value.strip() if field == "name" else value)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="saved_view.update",
        target_type="saved_view",
        target_id=view.id,
        meta={"name": view.name},
    )
    return view


async def delete_view(
    session: AsyncSession, workspace_id: str, view_id: str, *, actor: Actor, user_id: str | None
) -> None:
    view = await get_view(session, workspace_id, view_id, user_id=user_id)
    if view.visibility == ViewVisibility.PERSONAL.value and view.created_by != user_id:
        raise ForbiddenError("Cannot delete someone else's personal view")
    name = view.name
    await session.delete(view)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="saved_view.delete",
        target_type="saved_view",
        target_id=view_id,
        meta={"name": name},
    )
