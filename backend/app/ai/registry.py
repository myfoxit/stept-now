"""Provider registry: kind → adapter, workspace chat/embedding resolution.

Resolution NEVER raises for missing/broken configuration — chat falls back to
the deterministic mock provider, embeddings fall back to None (callers use the
local hash embedder). AI features must degrade, not 500.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import ChatProvider, EmbeddingProvider
from app.ai.local import MockChatProvider
from app.ai.providers.anthropic import DEFAULT_ANTHROPIC_BASE_URL, AnthropicProvider
from app.ai.providers.google import DEFAULT_GOOGLE_BASE_URL, GoogleProvider
from app.ai.providers.openai_compat import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OPENAI_BASE_URL,
    OpenAICompatProvider,
)
from app.core.logging import log
from app.core.security import decrypt_secret
from app.models.ai_provider import AiModel, AiProvider

logger = log("ai.registry")

MOCK_MODEL_KEY = "mock"

# Kinds whose HTTP surface is the OpenAI Chat Completions / embeddings API.
OPENAI_SHAPED_KINDS = frozenset({"openai", "openai_compatible", "ollama"})

# Known-model catalog per provider kind, exposed via GET /ai/catalog for UI
# pickers. openai_compatible/ollama are empty on purpose: the user supplies keys.
CATALOG: dict[str, list[dict[str, Any]]] = {
    "openai": [
        {
            "model_key": "gpt-4o",
            "display_name": "GPT-4o",
            "modality": "chat",
            "context_window": 128_000,
        },
        {
            "model_key": "gpt-4o-mini",
            "display_name": "GPT-4o mini",
            "modality": "chat",
            "context_window": 128_000,
        },
        {
            "model_key": "gpt-4.1",
            "display_name": "GPT-4.1",
            "modality": "chat",
            "context_window": 1_047_576,
        },
        {
            "model_key": "o3-mini",
            "display_name": "o3-mini",
            "modality": "chat",
            "context_window": 200_000,
        },
        {
            "model_key": "text-embedding-3-small",
            "display_name": "Text Embedding 3 Small",
            "modality": "embedding",
            "context_window": 8_191,
        },
    ],
    "anthropic": [
        {
            "model_key": "claude-opus-5",
            "display_name": "Claude Opus 5",
            "modality": "chat",
            "context_window": 1_000_000,
        },
        {
            "model_key": "claude-sonnet-5",
            "display_name": "Claude Sonnet 5",
            "modality": "chat",
            "context_window": 1_000_000,
        },
        {
            "model_key": "claude-haiku-4-5",
            "display_name": "Claude Haiku 4.5",
            "modality": "chat",
            "context_window": 200_000,
        },
    ],
    "google": [
        {
            "model_key": "gemini-2.5-pro",
            "display_name": "Gemini 2.5 Pro",
            "modality": "chat",
            "context_window": 1_048_576,
        },
        {
            "model_key": "gemini-2.5-flash",
            "display_name": "Gemini 2.5 Flash",
            "modality": "chat",
            "context_window": 1_048_576,
        },
    ],
    "openai_compatible": [],
    "ollama": [],
    "mock": [
        {
            "model_key": MOCK_MODEL_KEY,
            "display_name": "Mock (offline, deterministic)",
            "modality": "chat",
            "context_window": None,
        }
    ],
}

# Cheapest chat model per kind — used by the provider "test" endpoint.
CHEAPEST_CHAT_MODEL: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
    "google": "gemini-2.5-flash",
    "mock": MOCK_MODEL_KEY,
}


def build_chat_provider(provider: AiProvider) -> ChatProvider:
    """Instantiate the adapter for a provider row (decrypted key + base_url)."""
    kind = provider.kind
    if kind == "mock":
        return MockChatProvider()

    api_key = ""
    if provider.api_key_encrypted:
        api_key = decrypt_secret(provider.api_key_encrypted)

    if kind in OPENAI_SHAPED_KINDS:
        default_base = DEFAULT_OLLAMA_BASE_URL if kind == "ollama" else DEFAULT_OPENAI_BASE_URL
        return OpenAICompatProvider(
            api_key=api_key, base_url=provider.base_url or default_base, label=kind
        )
    if kind == "anthropic":
        return AnthropicProvider(
            api_key=api_key, base_url=provider.base_url or DEFAULT_ANTHROPIC_BASE_URL
        )
    if kind == "google":
        return GoogleProvider(
            api_key=api_key, base_url=provider.base_url or DEFAULT_GOOGLE_BASE_URL
        )
    logger.warning("unknown provider kind %r — falling back to mock", kind)
    return MockChatProvider()


async def _load_ref(
    session: AsyncSession, workspace_id: str, provider_id: str
) -> AiProvider | None:
    provider = await session.get(AiProvider, provider_id)
    if provider is None or provider.workspace_id != workspace_id or not provider.enabled:
        return None
    return provider


async def _default_pair(
    session: AsyncSession, workspace_id: str, modality: str
) -> tuple[AiProvider, AiModel] | None:
    row = (
        await session.execute(
            select(AiProvider, AiModel)
            .join(AiModel, AiModel.provider_id == AiProvider.id)
            .where(
                AiModel.workspace_id == workspace_id,
                AiModel.modality == modality,
                AiModel.is_default.is_(True),
                AiModel.enabled.is_(True),
                AiProvider.enabled.is_(True),
            )
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return row[0], row[1]


async def resolve_chat(
    session: AsyncSession, workspace_id: str, model_ref: str | None = None
) -> tuple[ChatProvider, str]:
    """Resolve a chat provider + model key for a workspace.

    `model_ref` is "provider_id:model_key" (or "mock"); None means the
    workspace's default chat model. Missing/broken config falls back to the
    mock provider — this function never raises for configuration problems.
    """
    try:
        if model_ref:
            if model_ref in (MOCK_MODEL_KEY, f"{MOCK_MODEL_KEY}:{MOCK_MODEL_KEY}"):
                return MockChatProvider(), MOCK_MODEL_KEY
            if ":" in model_ref:
                provider_id, model_key = model_ref.split(":", 1)
                provider = await _load_ref(session, workspace_id, provider_id)
                if provider is not None and model_key:
                    return build_chat_provider(provider), model_key
            logger.warning(
                "model_ref %r not resolvable in workspace %s — trying default",
                model_ref,
                workspace_id,
            )
        pair = await _default_pair(session, workspace_id, "chat")
        if pair is not None:
            provider_row, model = pair
            return build_chat_provider(provider_row), model.model_key
        logger.warning("workspace %s has no default chat model — using mock", workspace_id)
    except Exception:
        logger.warning(
            "chat resolution failed for workspace %s — using mock", workspace_id, exc_info=True
        )
    return MockChatProvider(), MOCK_MODEL_KEY


class RemoteEmbedder:
    """EmbeddingProvider bound to an openai-compatible /embeddings endpoint."""

    def __init__(self, provider: OpenAICompatProvider, model: str):
        self._provider = provider
        self.model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._provider.embed(texts, model=self.model)


async def resolve_workspace_embedder(
    session: AsyncSession, workspace_id: str
) -> EmbeddingProvider | None:
    """Workspace default embedding model → RemoteEmbedder; None if unconfigured.

    Callers (app.ai.embeddings) fall back to the local hash embedder on None.
    """
    try:
        pair = await _default_pair(session, workspace_id, "embedding")
        if pair is None:
            return None
        provider_row, model = pair
        if provider_row.kind not in OPENAI_SHAPED_KINDS:
            logger.warning(
                "embedding via kind %r is unsupported (workspace %s) — using local embedder",
                provider_row.kind,
                workspace_id,
            )
            return None
        adapter = build_chat_provider(provider_row)
        assert isinstance(adapter, OpenAICompatProvider)
        return RemoteEmbedder(adapter, model.model_key)
    except Exception:
        logger.warning(
            "embedder resolution failed for workspace %s — using local embedder",
            workspace_id,
            exc_info=True,
        )
        return None
