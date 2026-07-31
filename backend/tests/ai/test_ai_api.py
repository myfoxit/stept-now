"""AI provider/model endpoints: CRUD, masked keys, defaults, test, authz."""

from __future__ import annotations

import httpx
import respx
from sqlalchemy import select

from app.core.security import decrypt_secret
from app.models.ai_provider import AiModel, AiProvider
from tests.conftest import bearer, signup

PLAINTEXT_KEY = "sk-live-supersecret-abc4"


async def create_provider(client, ctx, **overrides):
    payload = {"kind": "openai", "name": "OpenAI", "api_key": PLAINTEXT_KEY}
    payload.update(overrides)
    response = await client.post(
        f"{ctx.base}/ai/providers", json=payload, headers=ctx.owner_headers
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_model(client, ctx, provider_id, **overrides):
    payload = {"model_key": "gpt-4o-mini", "modality": "chat"}
    payload.update(overrides)
    response = await client.post(
        f"{ctx.base}/ai/providers/{provider_id}/models",
        json=payload,
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


async def test_create_provider_masks_key_and_encrypts_at_rest(client, workspace_ctx, session):
    body = await create_provider(client, workspace_ctx)
    assert "api_key" not in body
    assert body["has_key"] is True
    assert body["api_key_hint"] == "…abc4"
    assert PLAINTEXT_KEY not in str(body)

    listing = await client.get(
        f"{workspace_ctx.base}/ai/providers", headers=workspace_ctx.owner_headers
    )
    assert listing.status_code == 200
    assert listing.json()[0]["api_key_hint"] == "…abc4"
    assert PLAINTEXT_KEY not in listing.text

    stored = (
        await session.execute(select(AiProvider).where(AiProvider.id == body["id"]))
    ).scalar_one()
    assert stored.api_key_encrypted is not None
    assert stored.api_key_encrypted != PLAINTEXT_KEY
    assert PLAINTEXT_KEY not in stored.api_key_encrypted
    assert decrypt_secret(stored.api_key_encrypted) == PLAINTEXT_KEY


async def test_patch_provider_rotates_and_clears_key(client, workspace_ctx, session):
    body = await create_provider(client, workspace_ctx)

    rotated = await client.patch(
        f"{workspace_ctx.base}/ai/providers/{body['id']}",
        json={"api_key": "sk-live-rotated-zzz9", "name": "OpenAI prod"},
        headers=workspace_ctx.owner_headers,
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["api_key_hint"] == "…zzz9"
    assert rotated.json()["name"] == "OpenAI prod"
    stored = (
        await session.execute(select(AiProvider).where(AiProvider.id == body["id"]))
    ).scalar_one()
    assert decrypt_secret(stored.api_key_encrypted) == "sk-live-rotated-zzz9"

    cleared = await client.patch(
        f"{workspace_ctx.base}/ai/providers/{body['id']}",
        json={"api_key": None},
        headers=workspace_ctx.owner_headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_key"] is False
    assert cleared.json()["api_key_hint"] is None


async def test_base_url_required_for_openai_compatible(client, workspace_ctx):
    missing = await client.post(
        f"{workspace_ctx.base}/ai/providers",
        json={"kind": "openai_compatible", "name": "vLLM"},
        headers=workspace_ctx.owner_headers,
    )
    assert missing.status_code == 400

    ok = await client.post(
        f"{workspace_ctx.base}/ai/providers",
        json={"kind": "openai_compatible", "name": "vLLM", "base_url": "http://vllm:8000/v1"},
        headers=workspace_ctx.owner_headers,
    )
    assert ok.status_code == 201

    unknown_kind = await client.post(
        f"{workspace_ctx.base}/ai/providers",
        json={"kind": "skynet", "name": "nope"},
        headers=workspace_ctx.owner_headers,
    )
    assert unknown_kind.status_code == 422


async def test_delete_provider_removes_its_models(client, workspace_ctx, session):
    body = await create_provider(client, workspace_ctx)
    await create_model(client, workspace_ctx, body["id"])

    deleted = await client.delete(
        f"{workspace_ctx.base}/ai/providers/{body['id']}", headers=workspace_ctx.owner_headers
    )
    assert deleted.status_code == 200, deleted.text

    remaining = await client.get(
        f"{workspace_ctx.base}/ai/providers", headers=workspace_ctx.owner_headers
    )
    assert remaining.json() == []
    orphans = (
        (await session.execute(select(AiModel).where(AiModel.provider_id == body["id"])))
        .scalars()
        .all()
    )
    assert orphans == []


async def test_cross_workspace_isolation(client, workspace_ctx):
    body = await create_provider(client, workspace_ctx)

    other_auth = await signup(client, "other-ai-owner@example.com")
    other_ws = (
        await client.post(
            "/api/v1/workspaces", json={"name": "Other AI Co"}, headers=bearer(other_auth)
        )
    ).json()

    stranger = await client.patch(
        f"/api/v1/w/{other_ws['id']}/ai/providers/{body['id']}",
        json={"name": "hijack"},
        headers=bearer(other_auth),
    )
    assert stranger.status_code == 404

    listing = await client.get(
        f"/api/v1/w/{other_ws['id']}/ai/providers", headers=bearer(other_auth)
    )
    assert listing.json() == []


async def test_agent_role_cannot_manage_but_can_read(client, workspace_ctx):
    agent_headers = await workspace_ctx.add_member("ai-agent@example.com", role="agent")

    forbidden = await client.post(
        f"{workspace_ctx.base}/ai/providers",
        json={"kind": "mock", "name": "Mock"},
        headers=agent_headers,
    )
    assert forbidden.status_code == 403

    readable = await client.get(f"{workspace_ctx.base}/ai/providers", headers=agent_headers)
    assert readable.status_code == 200
    catalog = await client.get(f"{workspace_ctx.base}/ai/catalog", headers=agent_headers)
    assert catalog.status_code == 200

    body = await create_provider(client, workspace_ctx, kind="mock", name="Mock", api_key=None)
    for method, url, payload in [
        ("patch", f"{workspace_ctx.base}/ai/providers/{body['id']}", {"name": "x"}),
        ("delete", f"{workspace_ctx.base}/ai/providers/{body['id']}", None),
        ("post", f"{workspace_ctx.base}/ai/providers/{body['id']}/test", {}),
        ("post", f"{workspace_ctx.base}/ai/providers/{body['id']}/models", {"model_key": "m"}),
    ]:
        kwargs = {"headers": agent_headers}
        if payload is not None:
            kwargs["json"] = payload
        response = await getattr(client, method)(url, **kwargs)
        assert response.status_code == 403, f"{method} {url} → {response.status_code}"


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


async def test_model_crud_and_default_uniqueness(client, workspace_ctx):
    provider = await create_provider(client, workspace_ctx)
    first = await create_model(
        client, workspace_ctx, provider["id"], model_key="gpt-4o", is_default=True
    )
    assert first["is_default"] is True
    assert first["display_name"] == "gpt-4o"  # defaults to model_key

    second = await create_model(
        client,
        workspace_ctx,
        provider["id"],
        model_key="gpt-4o-mini",
        display_name="GPT-4o mini",
        context_window=128000,
    )
    assert second["is_default"] is False

    # set-default clears the previous default of the same modality
    promoted = await client.post(
        f"{workspace_ctx.base}/ai/models/{second['id']}/set-default",
        headers=workspace_ctx.owner_headers,
    )
    assert promoted.status_code == 200
    assert promoted.json()["is_default"] is True
    models = await client.get(
        f"{workspace_ctx.base}/ai/providers/{provider['id']}/models",
        headers=workspace_ctx.owner_headers,
    )
    defaults = {m["model_key"]: m["is_default"] for m in models.json()}
    assert defaults == {"gpt-4o": False, "gpt-4o-mini": True}

    # creating with is_default=True also clears others
    third = await create_model(
        client, workspace_ctx, provider["id"], model_key="gpt-4.1", is_default=True
    )
    models = await client.get(
        f"{workspace_ctx.base}/ai/providers/{provider['id']}/models",
        headers=workspace_ctx.owner_headers,
    )
    defaults = {m["model_key"]: m["is_default"] for m in models.json()}
    assert defaults == {"gpt-4o": False, "gpt-4o-mini": False, "gpt-4.1": True}

    # embedding default lives in its own modality bucket
    embed = await create_model(
        client,
        workspace_ctx,
        provider["id"],
        model_key="text-embedding-3-small",
        modality="embedding",
        is_default=True,
    )
    assert embed["is_default"] is True
    models = await client.get(
        f"{workspace_ctx.base}/ai/providers/{provider['id']}/models",
        headers=workspace_ctx.owner_headers,
    )
    defaults = {m["model_key"]: m["is_default"] for m in models.json()}
    assert defaults["gpt-4.1"] is True  # untouched by the embedding default

    patched = await client.patch(
        f"{workspace_ctx.base}/ai/models/{third['id']}",
        json={"display_name": "GPT-4.1 (prod)", "enabled": False},
        headers=workspace_ctx.owner_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "GPT-4.1 (prod)"
    assert patched.json()["enabled"] is False

    removed = await client.delete(
        f"{workspace_ctx.base}/ai/models/{first['id']}", headers=workspace_ctx.owner_headers
    )
    assert removed.status_code == 200


async def test_duplicate_model_key_conflict(client, workspace_ctx):
    provider = await create_provider(client, workspace_ctx)
    await create_model(client, workspace_ctx, provider["id"], model_key="gpt-4o")
    duplicate = await client.post(
        f"{workspace_ctx.base}/ai/providers/{provider['id']}/models",
        json={"model_key": "gpt-4o"},
        headers=workspace_ctx.owner_headers,
    )
    assert duplicate.status_code == 409


async def test_flat_models_always_include_virtual_mock(client, workspace_ctx):
    empty = await client.get(f"{workspace_ctx.base}/ai/models", headers=workspace_ctx.owner_headers)
    assert empty.status_code == 200
    assert [m["model_key"] for m in empty.json()] == ["mock"]
    virtual = empty.json()[0]
    assert virtual["provider_kind"] == "mock"
    assert virtual["modality"] == "chat"
    assert virtual["id"] == "mock"

    provider = await create_provider(client, workspace_ctx)
    await create_model(client, workspace_ctx, provider["id"], model_key="gpt-4o", is_default=True)
    disabled = await create_model(client, workspace_ctx, provider["id"], model_key="gpt-4o-mini")
    await client.patch(
        f"{workspace_ctx.base}/ai/models/{disabled['id']}",
        json={"enabled": False},
        headers=workspace_ctx.owner_headers,
    )

    listing = await client.get(
        f"{workspace_ctx.base}/ai/models", headers=workspace_ctx.owner_headers
    )
    keys = [m["model_key"] for m in listing.json()]
    assert "gpt-4o" in keys
    assert "gpt-4o-mini" not in keys  # disabled models excluded
    assert "mock" in keys  # virtual entry still present
    real = next(m for m in listing.json() if m["model_key"] == "gpt-4o")
    assert real["provider_id"] == provider["id"]
    assert real["provider_kind"] == "openai"
    assert real["is_default"] is True


async def test_catalog_endpoint(client, workspace_ctx):
    response = await client.get(
        f"{workspace_ctx.base}/ai/catalog", headers=workspace_ctx.owner_headers
    )
    assert response.status_code == 200
    catalog = response.json()
    assert set(catalog) >= {"openai", "anthropic", "google", "openai_compatible", "ollama"}
    opus = next(m for m in catalog["anthropic"] if m["model_key"] == "claude-opus-5")
    assert opus["context_window"] == 1_000_000
    assert opus["modality"] == "chat"
    assert catalog["openai_compatible"] == []
    embedding_keys = [m["model_key"] for m in catalog["openai"] if m["modality"] == "embedding"]
    assert embedding_keys == ["text-embedding-3-small"]


# ---------------------------------------------------------------------------
# test endpoint
# ---------------------------------------------------------------------------


async def test_test_endpoint_mock_always_ok(client, workspace_ctx):
    provider = await create_provider(client, workspace_ctx, kind="mock", name="Mock", api_key=None)
    response = await client.post(
        f"{workspace_ctx.base}/ai/providers/{provider['id']}/test",
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["latency_ms"] >= 0


async def test_test_endpoint_pings_cheapest_model(client, workspace_ctx):
    provider = await create_provider(client, workspace_ctx)
    with respx.mock:
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {"index": 0, "message": {"content": "pong"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        )
        response = await client.post(
            f"{workspace_ctx.base}/ai/providers/{provider['id']}/test",
            headers=workspace_ctx.owner_headers,
        )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    import json as _json

    sent = _json.loads(route.calls.last.request.content)
    assert sent["model"] == "gpt-4o-mini"  # cheapest catalog model
    assert sent["max_tokens"] == 1


async def test_test_endpoint_compatible_kind_needs_a_model(client, workspace_ctx):
    provider = await create_provider(
        client,
        workspace_ctx,
        kind="openai_compatible",
        name="vLLM",
        base_url="http://vllm:8000/v1",
    )
    # No catalog and no configured models → informative failure, not a 500.
    response = await client.post(
        f"{workspace_ctx.base}/ai/providers/{provider['id']}/test",
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "model" in body["message"].lower()

    # With a configured chat model the test pings that model.
    await create_model(client, workspace_ctx, provider["id"], model_key="llama3")
    with respx.mock:
        route = respx.post("http://vllm:8000/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {"index": 0, "message": {"content": "pong"}, "finish_reason": "stop"}
                    ]
                },
            )
        )
        response = await client.post(
            f"{workspace_ctx.base}/ai/providers/{provider['id']}/test",
            headers=workspace_ctx.owner_headers,
        )
    assert response.json()["ok"] is True

    import json as _json

    assert _json.loads(route.calls.last.request.content)["model"] == "llama3"


async def test_test_endpoint_reports_failure_without_500(client, workspace_ctx):
    provider = await create_provider(client, workspace_ctx)
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
        )
        response = await client.post(
            f"{workspace_ctx.base}/ai/providers/{provider['id']}/test",
            json={"model_key": "gpt-4o"},
            headers=workspace_ctx.owner_headers,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "401" in body["message"]
    assert PLAINTEXT_KEY not in body["message"]
