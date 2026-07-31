"""Audit log service."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import Actor
from app.models.audit import AuditLog


async def record(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    meta: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        workspace_id=workspace_id,
        actor_type=actor.type,
        actor_id=actor.id,
        actor_label=actor.label,
        action=action,
        target_type=target_type,
        target_id=target_id,
        meta=meta or {},
        ip=ip,
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_entries(
    session: AsyncSession,
    workspace_id: str,
    *,
    action: str | None = None,
    actor_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    query = select(AuditLog).where(AuditLog.workspace_id == workspace_id)
    count_query = (
        select(func.count()).select_from(AuditLog).where(AuditLog.workspace_id == workspace_id)
    )
    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if actor_id:
        query = query.where(AuditLog.actor_id == actor_id)
        count_query = count_query.where(AuditLog.actor_id == actor_id)
    total = (await session.execute(count_query)).scalar_one()
    rows = (
        await session.execute(
            query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars()
    return list(rows), total
