"""AI provider/model schemas.

`api_key` is write-only: responses expose only `has_key` + a masked hint
(last four characters). The plaintext never leaves the service layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ORMModel

ProviderKindLiteral = Literal[
    "openai", "anthropic", "google", "openai_compatible", "ollama", "mock"
]
ModalityLiteral = Literal["chat", "embedding"]

# Schemas carry `model_key`/`model_ref` fields — disable pydantic's "model_" guard.
_NO_PROTECTED_NS = ConfigDict(protected_namespaces=())


class AiProviderCreate(BaseModel):
    kind: ProviderKindLiteral
    name: str = Field(min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = None  # write-only
    enabled: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)


class AiProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = None  # set to rotate; explicit null clears the stored key
    enabled: bool | None = None
    meta: dict[str, Any] | None = None


class AiProviderOut(BaseModel):
    id: str
    kind: str
    name: str
    base_url: str | None
    enabled: bool
    meta: dict[str, Any]
    has_key: bool
    api_key_hint: str | None  # "…abc4" — last 4 chars of the stored key
    created_at: datetime
    updated_at: datetime


class AiModelCreate(BaseModel):
    model_config = _NO_PROTECTED_NS

    model_key: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)  # defaults to model_key
    modality: ModalityLiteral = "chat"
    context_window: int | None = Field(default=None, gt=0)
    enabled: bool = True
    is_default: bool = False


class AiModelUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    context_window: int | None = Field(default=None, gt=0)
    enabled: bool | None = None


class AiModelOut(ORMModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    provider_id: str
    model_key: str
    display_name: str
    modality: str
    context_window: int | None
    enabled: bool
    is_default: bool
    created_at: datetime


class AiModelFlat(BaseModel):
    """Flat picker entry: enabled models across providers (+ the virtual mock)."""

    model_config = _NO_PROTECTED_NS

    id: str
    provider_id: str
    provider_kind: str
    provider_name: str
    model_key: str
    display_name: str
    modality: str
    is_default: bool


class ProviderTestRequest(BaseModel):
    model_config = _NO_PROTECTED_NS

    model_key: str | None = None


class ProviderTestResult(BaseModel):
    ok: bool
    message: str
    latency_ms: int


class CatalogModel(BaseModel):
    model_config = _NO_PROTECTED_NS

    model_key: str
    display_name: str
    modality: ModalityLiteral
    context_window: int | None = None
