"""AI provider/model management.

Keys are encrypted at rest and only ever surfaced as a masked hint. The mock
provider kind needs no key and no base_url; openai_compatible/ollama require an
explicit base_url.
"""

from __future__ import annotations

import time

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import ChatMessage, ChatRequest, ProviderError
from app.ai.registry import CHEAPEST_CHAT_MODEL, MOCK_MODEL_KEY, build_chat_provider
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.events import Actor
from app.core.security import decrypt_secret, encrypt_secret
from app.models.ai_provider import AiModel, AiProvider, Modality, ProviderKind
from app.schemas.ai_providers import (
    AiModelCreate,
    AiModelFlat,
    AiModelUpdate,
    AiProviderCreate,
    AiProviderOut,
    AiProviderUpdate,
)
from app.services import audit

_BASE_URL_REQUIRED_KINDS = {ProviderKind.OPENAI_COMPATIBLE, ProviderKind.OLLAMA}


def serialize_provider(provider: AiProvider) -> AiProviderOut:
    """API shape: never the key itself — only has_key + a "…abc4" hint."""
    hint: str | None = None
    if provider.api_key_encrypted:
        try:
            hint = f"…{decrypt_secret(provider.api_key_encrypted)[-4:]}"
        except Exception:  # noqa: BLE001 — secret key rotated; hint is best-effort
            hint = None
    return AiProviderOut(
        id=provider.id,
        kind=provider.kind,
        name=provider.name,
        base_url=provider.base_url,
        enabled=provider.enabled,
        meta=provider.meta,
        has_key=provider.api_key_encrypted is not None,
        api_key_hint=hint,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


async def list_providers(session: AsyncSession, workspace_id: str) -> list[AiProvider]:
    result = await session.execute(
        select(AiProvider)
        .where(AiProvider.workspace_id == workspace_id)
        .order_by(AiProvider.created_at)
    )
    return list(result.scalars())


async def get_provider(session: AsyncSession, workspace_id: str, provider_id: str) -> AiProvider:
    provider = await session.get(AiProvider, provider_id)
    if provider is None or provider.workspace_id != workspace_id:
        raise NotFoundError("AI provider not found")
    return provider


async def create_provider(
    session: AsyncSession, workspace_id: str, *, actor: Actor, data: AiProviderCreate
) -> AiProvider:
    if data.kind in _BASE_URL_REQUIRED_KINDS and not data.base_url:
        raise BadRequestError(f"base_url is required for kind '{data.kind}'")
    provider = AiProvider(
        workspace_id=workspace_id,
        kind=data.kind,
        name=data.name.strip(),
        base_url=data.base_url,
        api_key_encrypted=encrypt_secret(data.api_key) if data.api_key else None,
        enabled=data.enabled,
        meta=data.meta,
    )
    session.add(provider)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="ai_provider.create",
        target_type="ai_provider",
        target_id=provider.id,
        meta={"kind": provider.kind, "name": provider.name},
    )
    return provider


async def update_provider(
    session: AsyncSession,
    workspace_id: str,
    provider_id: str,
    *,
    actor: Actor,
    data: AiProviderUpdate,
) -> AiProvider:
    provider = await get_provider(session, workspace_id, provider_id)
    fields = data.model_fields_set
    rotated_key = False

    if "name" in fields and data.name is not None:
        provider.name = data.name.strip()
    if "base_url" in fields:
        if provider.kind in _BASE_URL_REQUIRED_KINDS and not data.base_url:
            raise BadRequestError(f"base_url is required for kind '{provider.kind}'")
        provider.base_url = data.base_url
    if "api_key" in fields:  # rotate on a value, clear on explicit null/empty
        provider.api_key_encrypted = encrypt_secret(data.api_key) if data.api_key else None
        rotated_key = True
    if "enabled" in fields and data.enabled is not None:
        provider.enabled = data.enabled
    if "meta" in fields and data.meta is not None:
        provider.meta = data.meta

    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="ai_provider.update",
        target_type="ai_provider",
        target_id=provider.id,
        meta={"name": provider.name, "rotated_key": rotated_key},
    )
    return provider


async def delete_provider(
    session: AsyncSession, workspace_id: str, provider_id: str, *, actor: Actor
) -> None:
    provider = await get_provider(session, workspace_id, provider_id)
    # Explicit model cleanup: SQLite does not enforce FK ON DELETE CASCADE.
    await session.execute(delete(AiModel).where(AiModel.provider_id == provider.id))
    name, kind = provider.name, provider.kind
    await session.delete(provider)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="ai_provider.delete",
        target_type="ai_provider",
        target_id=provider_id,
        meta={"kind": kind, "name": name},
    )


async def test_provider(
    session: AsyncSession, workspace_id: str, provider_id: str, *, model_key: str | None = None
) -> tuple[bool, str, int]:
    """1-token "ping" generate against the provider; (ok, message, latency_ms)."""
    provider = await get_provider(session, workspace_id, provider_id)
    started = time.perf_counter()

    def latency() -> int:
        return int((time.perf_counter() - started) * 1000)

    if provider.kind == ProviderKind.MOCK:
        return True, "Mock provider is always available.", latency()

    model = model_key or await _test_model_for(session, provider)
    if model is None:
        return False, "No model to test — add a model or pass model_key.", latency()
    try:
        chat = build_chat_provider(provider)
        await chat.generate(
            ChatRequest(
                model=model, messages=[ChatMessage.user("ping")], max_tokens=1, temperature=0.0
            )
        )
    except ProviderError as exc:
        return False, str(exc), latency()
    except Exception as exc:  # noqa: BLE001 — test endpoint must not 500
        return False, f"{type(exc).__name__}: {exc}", latency()
    return True, f"Reached {provider.kind} with model {model}.", latency()


async def _test_model_for(session: AsyncSession, provider: AiProvider) -> str | None:
    if provider.kind in CHEAPEST_CHAT_MODEL:
        return CHEAPEST_CHAT_MODEL[provider.kind]
    configured = (
        await session.execute(
            select(AiModel.model_key)
            .where(
                AiModel.provider_id == provider.id,
                AiModel.modality == Modality.CHAT,
                AiModel.enabled.is_(True),
            )
            .order_by(AiModel.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    return configured


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


async def get_model(session: AsyncSession, workspace_id: str, model_id: str) -> AiModel:
    model = await session.get(AiModel, model_id)
    if model is None or model.workspace_id != workspace_id:
        raise NotFoundError("AI model not found")
    return model


async def list_models(session: AsyncSession, workspace_id: str, provider_id: str) -> list[AiModel]:
    await get_provider(session, workspace_id, provider_id)
    result = await session.execute(
        select(AiModel)
        .where(AiModel.workspace_id == workspace_id, AiModel.provider_id == provider_id)
        .order_by(AiModel.created_at)
    )
    return list(result.scalars())


async def create_model(
    session: AsyncSession,
    workspace_id: str,
    provider_id: str,
    *,
    actor: Actor,
    data: AiModelCreate,
) -> AiModel:
    await get_provider(session, workspace_id, provider_id)
    duplicate = (
        await session.execute(
            select(AiModel.id).where(
                AiModel.provider_id == provider_id, AiModel.model_key == data.model_key
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ConflictError(f"Model '{data.model_key}' already exists for this provider")

    model = AiModel(
        workspace_id=workspace_id,
        provider_id=provider_id,
        model_key=data.model_key,
        display_name=data.display_name or data.model_key,
        modality=data.modality,
        context_window=data.context_window,
        enabled=data.enabled,
        is_default=data.is_default,
    )
    session.add(model)
    await session.flush()
    if model.is_default:
        await _clear_other_defaults(session, workspace_id, model.modality, keep_id=model.id)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="ai_model.create",
        target_type="ai_model",
        target_id=model.id,
        meta={"model_key": model.model_key, "modality": model.modality},
    )
    return model


async def update_model(
    session: AsyncSession, workspace_id: str, model_id: str, *, actor: Actor, data: AiModelUpdate
) -> AiModel:
    model = await get_model(session, workspace_id, model_id)
    fields = data.model_fields_set
    if "display_name" in fields and data.display_name is not None:
        model.display_name = data.display_name
    if "context_window" in fields:
        model.context_window = data.context_window
    if "enabled" in fields and data.enabled is not None:
        model.enabled = data.enabled
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="ai_model.update",
        target_type="ai_model",
        target_id=model.id,
        meta={"model_key": model.model_key},
    )
    return model


async def delete_model(
    session: AsyncSession, workspace_id: str, model_id: str, *, actor: Actor
) -> None:
    model = await get_model(session, workspace_id, model_id)
    model_key = model.model_key
    await session.delete(model)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="ai_model.delete",
        target_type="ai_model",
        target_id=model_id,
        meta={"model_key": model_key},
    )


async def set_default_model(
    session: AsyncSession, workspace_id: str, model_id: str, *, actor: Actor
) -> AiModel:
    """Make this model the workspace default for its modality (clears others)."""
    model = await get_model(session, workspace_id, model_id)
    model.is_default = True
    await _clear_other_defaults(session, workspace_id, model.modality, keep_id=model.id)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="ai_model.set_default",
        target_type="ai_model",
        target_id=model.id,
        meta={"model_key": model.model_key, "modality": model.modality},
    )
    return model


async def _clear_other_defaults(
    session: AsyncSession, workspace_id: str, modality: str, *, keep_id: str
) -> None:
    await session.execute(
        update(AiModel)
        .where(
            AiModel.workspace_id == workspace_id,
            AiModel.modality == modality,
            AiModel.is_default.is_(True),
            AiModel.id != keep_id,
        )
        .values(is_default=False)
    )


# ---------------------------------------------------------------------------
# picker helpers
# ---------------------------------------------------------------------------


async def list_enabled_models_flat(session: AsyncSession, workspace_id: str) -> list[AiModelFlat]:
    """Flat enabled models across enabled providers + a virtual mock entry."""
    rows = (
        await session.execute(
            select(AiModel, AiProvider)
            .join(AiProvider, AiModel.provider_id == AiProvider.id)
            .where(
                AiModel.workspace_id == workspace_id,
                AiModel.enabled.is_(True),
                AiProvider.enabled.is_(True),
            )
            .order_by(AiModel.created_at)
        )
    ).all()
    entries = [
        AiModelFlat(
            id=model.id,
            provider_id=provider.id,
            provider_kind=provider.kind,
            provider_name=provider.name,
            model_key=model.model_key,
            display_name=model.display_name,
            modality=model.modality,
            is_default=model.is_default,
        )
        for model, provider in rows
    ]
    # Pickers must never come up empty: always offer the offline mock model.
    has_mock = any(
        e.provider_kind == ProviderKind.MOCK and e.model_key == MOCK_MODEL_KEY for e in entries
    )
    if not has_mock:
        entries.append(
            AiModelFlat(
                id=MOCK_MODEL_KEY,
                provider_id=MOCK_MODEL_KEY,
                provider_kind=ProviderKind.MOCK,
                provider_name="Mock (offline)",
                model_key=MOCK_MODEL_KEY,
                display_name="Mock (offline, deterministic)",
                modality=Modality.CHAT,
                is_default=False,
            )
        )
    return entries
