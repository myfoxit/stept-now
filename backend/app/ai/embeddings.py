"""Embedding entry point used by the RAG pipeline.

Wave 1 agent C extends `resolve_embedding_provider` to honor per-workspace
provider configuration; the local hash embedder is the always-available default,
so ingestion/retrieval never block on missing API keys.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import EmbeddingProvider
from app.ai.local import LocalHashEmbedder

_local = LocalHashEmbedder()


async def resolve_embedding_provider(session: AsyncSession, workspace_id: str) -> EmbeddingProvider:
    """Workspace-configured embedding provider, else the local hash embedder."""
    try:  # Wave 1 C wires DB-configured providers here.
        from app.ai.registry import resolve_workspace_embedder  # type: ignore[attr-defined]
    except ImportError:
        return _local
    provider = await resolve_workspace_embedder(session, workspace_id)
    return provider or _local


async def embed_texts(
    session: AsyncSession, workspace_id: str, texts: list[str]
) -> list[list[float]]:
    if not texts:
        return []
    provider = await resolve_embedding_provider(session, workspace_id)
    return await provider.embed(texts)
