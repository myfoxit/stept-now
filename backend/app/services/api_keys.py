"""API key management."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import BadRequestError, NotFoundError
from app.core.events import Actor
from app.core.permissions import API_KEY_SCOPES
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.services import audit


async def create_key(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    name: str,
    scopes: list[str],
) -> tuple[ApiKey, str]:
    unknown = [s for s in scopes if s not in API_KEY_SCOPES]
    if unknown:
        raise BadRequestError(f"Unknown scopes: {', '.join(unknown)}")
    if not scopes:
        raise BadRequestError("At least one scope is required")
    full, prefix, hashed = generate_api_key()
    api_key = ApiKey(
        workspace_id=workspace_id,
        name=name.strip(),
        prefix=prefix,
        hashed_key=hashed,
        scopes=sorted(set(scopes)),
        created_by=actor.id,
    )
    session.add(api_key)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="api_key.create",
        target_type="api_key",
        target_id=api_key.id,
        meta={"name": name, "scopes": scopes},
    )
    return api_key, full


async def list_keys(session: AsyncSession, workspace_id: str) -> list[ApiKey]:
    result = await session.execute(
        select(ApiKey).where(ApiKey.workspace_id == workspace_id).order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars())


async def revoke_key(
    session: AsyncSession, workspace_id: str, key_id: str, *, actor: Actor
) -> ApiKey:
    api_key = await session.get(ApiKey, key_id)
    if api_key is None or api_key.workspace_id != workspace_id:
        raise NotFoundError("API key not found")
    if api_key.revoked_at is None:
        api_key.revoked_at = utcnow()
        await audit.record(
            session,
            workspace_id,
            actor=actor,
            action="api_key.revoke",
            target_type="api_key",
            target_id=key_id,
            meta={"name": api_key.name},
        )
    return api_key
