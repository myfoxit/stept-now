"""Workspace lifecycle + membership resolution."""

from __future__ import annotations

import re
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import uuid7
from app.core.errors import ForbiddenError, NotFoundError
from app.core.events import Actor, Event, EventNames, emit
from app.core.permissions import resolve_permissions
from app.core.security import new_token
from app.models.user import User
from app.models.workspace import Membership, Workspace


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "workspace"


async def create_workspace(session: AsyncSession, user: User, *, name: str) -> Workspace:
    slug = slugify(name)
    exists = (
        await session.execute(select(Workspace.id).where(Workspace.slug == slug))
    ).scalar_one_or_none()
    if exists is not None:
        slug = f"{slug}-{secrets.token_hex(3)}"
    workspace = Workspace(
        id=uuid7(),
        name=name.strip(),
        slug=slug,
        created_by=user.id,
        settings={
            "identity_secret": new_token(24),  # widget identity verification (HMAC)
            "timezone": "UTC",
        },
    )
    session.add(workspace)
    # Flush the workspace on its own before anything references it. Membership
    # has no relationship() to Workspace — only a raw workspace_id FK — and the
    # unit of work orders inserts from mapper relationships, not from column
    # ForeignKeys, so a single combined flush is free to write memberships first.
    # Postgres then rejects it; SQLite does not enforce FKs by default and lets
    # it pass, which is why this only ever failed in production.
    await session.flush()
    session.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    await session.flush()
    await emit(
        session,
        Event(
            name=EventNames.WORKSPACE_CREATED,
            workspace_id=workspace.id,
            payload={"name": workspace.name},
            actor=Actor(type="user", id=user.id, label=user.name),
        ),
    )
    return workspace


async def list_my_workspaces(session: AsyncSession, user: User) -> list[Membership]:
    result = await session.execute(
        select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at)
    )
    return list(result.scalars())


async def get_membership(session: AsyncSession, workspace_id: str, user_id: str) -> Membership:
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise ForbiddenError("No access to this workspace")
    return membership


def membership_permissions(membership: Membership) -> list[str]:
    custom = (
        list(membership.custom_role.permissions)
        if membership.role == "custom" and membership.custom_role is not None
        else None
    )
    return sorted(p.value for p in resolve_permissions(membership.role, custom))


async def update_workspace(
    session: AsyncSession,
    workspace: Workspace,
    *,
    name: str | None = None,
    logo_url: str | None = None,
    settings_patch: dict | None = None,
) -> Workspace:
    if name is not None:
        workspace.name = name.strip()
    if logo_url is not None:
        workspace.logo_url = logo_url
    if settings_patch is not None:
        merged = dict(workspace.settings)
        merged.update(settings_patch)
        # identity_secret is never settable through the API
        merged["identity_secret"] = workspace.settings.get("identity_secret")
        workspace.settings = merged
    await session.flush()
    return workspace


async def delete_workspace(session: AsyncSession, workspace_id: str) -> None:
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError("Workspace not found")
    await session.delete(workspace)
