"""API key endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.services import api_keys as service

router = APIRouter(dependencies=[Depends(require_perm(Perm.APIKEYS_MANAGE))])


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_keys(principal: Member, session: Db):
    keys = await service.list_keys(session, principal.workspace.id)
    return [ApiKeyOut.model_validate(k) for k in keys]


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_key(body: ApiKeyCreate, principal: Member, session: Db):
    actor = Actor(type=principal.kind, id=principal.actor_id, label=principal.label)
    api_key, full = await service.create_key(
        session,
        principal.workspace.id,
        actor=actor,
        name=body.name,
        scopes=body.scopes,
        agent_id=body.agent_id,
    )
    return ApiKeyCreated(**ApiKeyOut.model_validate(api_key).model_dump(), key=full)


@router.delete("/api-keys/{key_id}", response_model=ApiKeyOut)
async def revoke_key(key_id: str, principal: Member, session: Db):
    actor = Actor(type=principal.kind, id=principal.actor_id, label=principal.label)
    api_key = await service.revoke_key(session, principal.workspace.id, key_id, actor=actor)
    return ApiKeyOut.model_validate(api_key)
