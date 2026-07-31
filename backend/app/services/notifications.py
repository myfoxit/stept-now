"""In-app notifications + realtime push to the member's socket."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.pubsub import get_pubsub
from app.models.notification import Notification


async def notify(
    session: AsyncSession,
    workspace_id: str,
    user_id: str,
    *,
    type: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Notification:
    notification = Notification(
        workspace_id=workspace_id,
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        link=link,
        meta=meta or {},
    )
    session.add(notification)
    await session.flush()
    await get_pubsub().publish(
        f"ws:{workspace_id}:user:{user_id}",
        {
            "type": "notification.created",
            "data": {
                "id": notification.id,
                "notification_type": type,
                "title": title,
                "body": body,
                "link": link,
            },
        },
    )
    return notification


async def list_for_user(
    session: AsyncSession,
    workspace_id: str,
    user_id: str,
    *,
    unread_only: bool = False,
    limit: int = 50,
) -> list[Notification]:
    query = select(Notification).where(
        Notification.workspace_id == workspace_id, Notification.user_id == user_id
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    result = await session.execute(query.order_by(Notification.created_at.desc()).limit(limit))
    return list(result.scalars())


async def mark_read(
    session: AsyncSession, workspace_id: str, user_id: str, notification_id: str
) -> None:
    await session.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.workspace_id == workspace_id,
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )


async def mark_all_read(session: AsyncSession, workspace_id: str, user_id: str) -> None:
    await session.execute(
        update(Notification)
        .where(
            Notification.workspace_id == workspace_id,
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
