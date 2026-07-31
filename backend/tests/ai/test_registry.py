"""Registry: kind → adapter build, workspace resolution + mock fallback, seed."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import respx
from sqlalchemy import select

from app.ai.local import MockChatProvider
from app.ai.providers.anthropic import AnthropicProvider
from app.ai.providers.google import GoogleProvider
from app.ai.providers.openai_compat import OpenAICompatProvider
from app.ai.registry import (
    RemoteEmbedder,
    build_chat_provider,
    resolve_chat,
    resolve_workspace_embedder,
)
from app.ai.seed import seed as seed_ai
from app.core.db import uuid7
from app.core.security import encrypt_secret
from app.models.ai_provider import AiModel, AiProvider
from app.models.workspace import Workspace


async def make_workspace(session) -> Workspace:
    workspace = Workspace(name="AI Test", slug=f"ai-test-{uuid7()}")
    session.add(workspace)
    await session.flush()
    return workspace


def provider_row(workspace_id: str, kind: str, **overrides) -> AiProvider:
    defaults: dict = {
        "workspace_id": workspace_id,
        "kind": kind,
        "name": f"{kind} provider",
        "api_key_encrypted": encrypt_secret("plain-key-1234"),
        "enabled": True,
        "meta": {},
    }
    defaults.update(overrides)
    return AiProvider(**defaults)


def test_build_chat_provider_per_kind_decrypts_key():
    workspace_id = uuid7()
    openai = build_chat_provider(provider_row(workspace_id, "openai"))
    assert isinstance(openai, OpenAICompatProvider)
    assert openai.api_key == "plain-key-1234"  # decrypted from storage
    assert openai.base_url == "https://api.openai.com/v1"

    ollama = build_chat_provider(
        provider_row(workspace_id, "ollama", base_url="http://gpu-box:11434/v1")
    )
    assert isinstance(ollama, OpenAICompatProvider)
    assert ollama.base_url == "http://gpu-box:11434/v1"

    anthropic = build_chat_provider(provider_row(workspace_id, "anthropic"))
    assert isinstance(anthropic, AnthropicProvider)
    assert anthropic.api_key == "plain-key-1234"

    google = build_chat_provider(provider_row(workspace_id, "google"))
    assert isinstance(google, GoogleProvider)

    mock = build_chat_provider(provider_row(workspace_id, "mock", api_key_encrypted=None))
    assert isinstance(mock, MockChatProvider)

    unknown = build_chat_provider(provider_row(workspace_id, "martian"))
    assert isinstance(unknown, MockChatProvider)  # never raises


async def test_resolve_chat_falls_back_to_mock_without_config(db_only):
    workspace = await make_workspace(db_only)
    provider, model_key = await resolve_chat(db_only, workspace.id, None)
    assert isinstance(provider, MockChatProvider)
    assert model_key == "mock"


async def test_resolve_chat_uses_workspace_default(db_only):
    workspace = await make_workspace(db_only)
    row = provider_row(workspace.id, "anthropic")
    db_only.add(row)
    await db_only.flush()
    db_only.add(
        AiModel(
            workspace_id=workspace.id,
            provider_id=row.id,
            model_key="claude-haiku-4-5",
            display_name="Haiku",
            modality="chat",
            enabled=True,
            is_default=True,
        )
    )
    await db_only.flush()

    provider, model_key = await resolve_chat(db_only, workspace.id, None)
    assert isinstance(provider, AnthropicProvider)
    assert model_key == "claude-haiku-4-5"


async def test_resolve_chat_model_ref_and_bad_ref(db_only):
    workspace = await make_workspace(db_only)
    row = provider_row(workspace.id, "google")
    db_only.add(row)
    await db_only.flush()

    provider, model_key = await resolve_chat(db_only, workspace.id, f"{row.id}:gemini-2.5-pro")
    assert isinstance(provider, GoogleProvider)
    assert model_key == "gemini-2.5-pro"

    # explicit mock ref
    provider, model_key = await resolve_chat(db_only, workspace.id, "mock")
    assert isinstance(provider, MockChatProvider)

    # unknown provider id → mock, never raises
    provider, model_key = await resolve_chat(db_only, workspace.id, f"{uuid7()}:whatever")
    assert isinstance(provider, MockChatProvider)
    assert model_key == "mock"

    # provider from ANOTHER workspace must not resolve
    other = await make_workspace(db_only)
    provider, model_key = await resolve_chat(db_only, other.id, f"{row.id}:gemini-2.5-pro")
    assert isinstance(provider, MockChatProvider)


async def test_resolve_chat_ignores_disabled_provider(db_only):
    workspace = await make_workspace(db_only)
    row = provider_row(workspace.id, "openai", enabled=False)
    db_only.add(row)
    await db_only.flush()
    db_only.add(
        AiModel(
            workspace_id=workspace.id,
            provider_id=row.id,
            model_key="gpt-4o",
            display_name="GPT-4o",
            modality="chat",
            enabled=True,
            is_default=True,
        )
    )
    await db_only.flush()

    provider, model_key = await resolve_chat(db_only, workspace.id, None)
    assert isinstance(provider, MockChatProvider)
    assert model_key == "mock"


async def test_resolve_workspace_embedder(db_only):
    workspace = await make_workspace(db_only)
    assert await resolve_workspace_embedder(db_only, workspace.id) is None  # unconfigured

    row = provider_row(workspace.id, "openai")
    db_only.add(row)
    await db_only.flush()
    db_only.add(
        AiModel(
            workspace_id=workspace.id,
            provider_id=row.id,
            model_key="text-embedding-3-small",
            display_name="Embed small",
            modality="embedding",
            enabled=True,
            is_default=True,
        )
    )
    await db_only.flush()

    embedder = await resolve_workspace_embedder(db_only, workspace.id)
    assert isinstance(embedder, RemoteEmbedder)
    assert embedder.model == "text-embedding-3-small"

    with respx.mock:
        respx.post("https://api.openai.com/v1/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5, 0.5]}]})
        )
        vectors = await embedder.embed(["hello"])
    assert vectors == [[0.5, 0.5]]


async def test_resolve_workspace_embedder_unsupported_kind_returns_none(db_only):
    workspace = await make_workspace(db_only)
    row = provider_row(workspace.id, "anthropic")
    db_only.add(row)
    await db_only.flush()
    db_only.add(
        AiModel(
            workspace_id=workspace.id,
            provider_id=row.id,
            model_key="not-a-real-embedder",
            display_name="Nope",
            modality="embedding",
            enabled=True,
            is_default=True,
        )
    )
    await db_only.flush()
    assert await resolve_workspace_embedder(db_only, workspace.id) is None


async def test_embeddings_entrypoint_falls_back_to_local(db_only):
    from app.ai.embeddings import resolve_embedding_provider
    from app.ai.local import LocalHashEmbedder

    workspace = await make_workspace(db_only)
    provider = await resolve_embedding_provider(db_only, workspace.id)
    assert isinstance(provider, LocalHashEmbedder)


async def test_seed_is_idempotent_and_sets_default(db_only):
    workspace = await make_workspace(db_only)
    ctx = SimpleNamespace(workspace=workspace)
    await seed_ai(db_only, ctx)
    await seed_ai(db_only, ctx)  # second run must not duplicate

    providers = (
        (await db_only.execute(select(AiProvider).where(AiProvider.workspace_id == workspace.id)))
        .scalars()
        .all()
    )
    assert len(providers) == 1
    assert providers[0].kind == "mock"

    models = (
        (await db_only.execute(select(AiModel).where(AiModel.workspace_id == workspace.id)))
        .scalars()
        .all()
    )
    assert len(models) == 1
    assert models[0].model_key == "mock"
    assert models[0].is_default is True

    provider, model_key = await resolve_chat(db_only, workspace.id, None)
    assert isinstance(provider, MockChatProvider)
    assert model_key == "mock"
