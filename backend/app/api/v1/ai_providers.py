"""AI provider/model endpoints (ai:read for GETs, ai:manage for mutations)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.ai.registry import CATALOG
from app.core.deps import Db, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.schemas.ai_providers import (
    AiModelCreate,
    AiModelFlat,
    AiModelOut,
    AiModelUpdate,
    AiProviderCreate,
    AiProviderOut,
    AiProviderUpdate,
    CatalogModel,
    ProviderTestRequest,
    ProviderTestResult,
)
from app.schemas.common import Msg
from app.services import ai_providers as service

router = APIRouter()

Reader = Annotated[Principal, Depends(require_perm(Perm.AI_READ))]
Manager = Annotated[Principal, Depends(require_perm(Perm.AI_MANAGE))]


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


# ---------------------------------------------------------------------------
# catalog + flat model picker
# ---------------------------------------------------------------------------


@router.get("/ai/catalog", response_model=dict[str, list[CatalogModel]])
async def get_catalog(principal: Reader) -> Any:
    return CATALOG


@router.get("/ai/models", response_model=list[AiModelFlat])
async def list_models_flat(principal: Reader, session: Db):
    return await service.list_enabled_models_flat(session, principal.workspace.id)


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


@router.get("/ai/providers", response_model=list[AiProviderOut])
async def list_providers(principal: Reader, session: Db):
    providers = await service.list_providers(session, principal.workspace.id)
    return [service.serialize_provider(p) for p in providers]


@router.post("/ai/providers", response_model=AiProviderOut, status_code=201)
async def create_provider(body: AiProviderCreate, principal: Manager, session: Db):
    provider = await service.create_provider(
        session, principal.workspace.id, actor=_actor(principal), data=body
    )
    return service.serialize_provider(provider)


@router.patch("/ai/providers/{provider_id}", response_model=AiProviderOut)
async def update_provider(
    provider_id: str, body: AiProviderUpdate, principal: Manager, session: Db
):
    provider = await service.update_provider(
        session, principal.workspace.id, provider_id, actor=_actor(principal), data=body
    )
    return service.serialize_provider(provider)


@router.delete("/ai/providers/{provider_id}", response_model=Msg)
async def delete_provider(provider_id: str, principal: Manager, session: Db):
    await service.delete_provider(
        session, principal.workspace.id, provider_id, actor=_actor(principal)
    )
    return Msg(message="AI provider deleted")


@router.post("/ai/providers/{provider_id}/test", response_model=ProviderTestResult)
async def test_provider(
    provider_id: str,
    principal: Manager,
    session: Db,
    body: ProviderTestRequest | None = None,
):
    ok, message, latency_ms = await service.test_provider(
        session,
        principal.workspace.id,
        provider_id,
        model_key=body.model_key if body else None,
    )
    return ProviderTestResult(ok=ok, message=message, latency_ms=latency_ms)


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


@router.get("/ai/providers/{provider_id}/models", response_model=list[AiModelOut])
async def list_provider_models(provider_id: str, principal: Reader, session: Db):
    models = await service.list_models(session, principal.workspace.id, provider_id)
    return [AiModelOut.model_validate(m) for m in models]


@router.post("/ai/providers/{provider_id}/models", response_model=AiModelOut, status_code=201)
async def create_model(provider_id: str, body: AiModelCreate, principal: Manager, session: Db):
    model = await service.create_model(
        session, principal.workspace.id, provider_id, actor=_actor(principal), data=body
    )
    return AiModelOut.model_validate(model)


@router.patch("/ai/models/{model_id}", response_model=AiModelOut)
async def update_model(model_id: str, body: AiModelUpdate, principal: Manager, session: Db):
    model = await service.update_model(
        session, principal.workspace.id, model_id, actor=_actor(principal), data=body
    )
    return AiModelOut.model_validate(model)


@router.delete("/ai/models/{model_id}", response_model=Msg)
async def delete_model(model_id: str, principal: Manager, session: Db):
    await service.delete_model(session, principal.workspace.id, model_id, actor=_actor(principal))
    return Msg(message="AI model deleted")


@router.post("/ai/models/{model_id}/set-default", response_model=AiModelOut)
async def set_default_model(model_id: str, principal: Manager, session: Db):
    model = await service.set_default_model(
        session, principal.workspace.id, model_id, actor=_actor(principal)
    )
    return AiModelOut.model_validate(model)
