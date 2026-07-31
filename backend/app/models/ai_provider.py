"""AI providers and models (workspace-configured LLM/embedding endpoints).

API keys are encrypted at rest (`encrypt_secret`); the plaintext is never stored
or logged. The "mock" provider kind works without any row, but a row is seeded
for visibility in the UI.
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class ProviderKind(enum.StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"
    MOCK = "mock"


class Modality(enum.StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"


class AiProvider(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "ai_providers"

    id: Mapped[str] = pk()
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # ProviderKind values
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Required for openai_compatible/ollama; optional override elsewhere.
    base_url: Mapped[str | None] = mapped_column(String(500))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


class AiModel(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "ai_models"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_key", name="uq_ai_models_provider_model"),
    )

    id: Mapped[str] = pk()
    provider_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("ai_providers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    model_key: Mapped[str] = mapped_column(String(200), nullable=False)  # e.g. "claude-opus-5"
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    modality: Mapped[str] = mapped_column(String(20), nullable=False)  # Modality values
    context_window: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Max one default per (workspace, modality) — enforced by the service layer.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
