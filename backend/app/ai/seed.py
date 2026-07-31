"""AI seed: mock provider row + its default chat model (idempotent).

No embedding model is seeded on purpose — with none configured, the RAG
pipeline falls back to the local hash embedder (see app.ai.embeddings).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.registry import MOCK_MODEL_KEY
from app.models.ai_provider import AiModel, AiProvider, Modality, ProviderKind

if TYPE_CHECKING:
    from app.seed import SeedContext


async def seed(session: AsyncSession, ctx: SeedContext) -> None:
    workspace_id = ctx.workspace.id

    provider = (
        (
            await session.execute(
                select(AiProvider).where(
                    AiProvider.workspace_id == workspace_id, AiProvider.kind == ProviderKind.MOCK
                )
            )
        )
        .scalars()
        .first()
    )
    if provider is None:
        # The mock provider works without a row — this one exists for UI visibility.
        provider = AiProvider(
            workspace_id=workspace_id,
            kind=ProviderKind.MOCK,
            name="Mock (offline)",
            enabled=True,
            meta={"seeded": True},
        )
        session.add(provider)
        await session.flush()

    model = (
        (
            await session.execute(
                select(AiModel).where(
                    AiModel.provider_id == provider.id, AiModel.model_key == MOCK_MODEL_KEY
                )
            )
        )
        .scalars()
        .first()
    )
    if model is None:
        has_chat_default = (
            await session.execute(
                select(AiModel.id)
                .where(
                    AiModel.workspace_id == workspace_id,
                    AiModel.modality == Modality.CHAT,
                    AiModel.is_default.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        session.add(
            AiModel(
                workspace_id=workspace_id,
                provider_id=provider.id,
                model_key=MOCK_MODEL_KEY,
                display_name="Mock (offline, deterministic)",
                modality=Modality.CHAT,
                enabled=True,
                is_default=not has_chat_default,
            )
        )
        await session.flush()
