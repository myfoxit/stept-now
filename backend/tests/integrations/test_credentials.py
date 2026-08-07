"""App credentials: write-only secrets, extra merge, env fallback, listing shape."""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.integration import IntegrationAppCredential
from tests.integrations.conftest import (
    mint_authorize_url,
    put_credentials,
    set_env_credentials,
)


async def _providers_by_id(client, workspace_ctx) -> dict:
    response = await client.get(
        f"{workspace_ctx.base}/integrations", headers=workspace_ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    return {p["id"]: p for p in response.json()["providers"]}


async def _credential_row(workspace_id: str, provider: str) -> IntegrationAppCredential | None:
    async with get_session_factory()() as session:
        return (
            await session.execute(
                select(IntegrationAppCredential).where(
                    IntegrationAppCredential.workspace_id == workspace_id,
                    IntegrationAppCredential.provider == provider,
                )
            )
        ).scalar_one_or_none()


async def test_put_stores_secret_write_only(client, workspace_ctx):
    body = await put_credentials(workspace_ctx, "google", client_secret="super-secret")
    assert body["has_secret"] is True
    assert body["from_env"] is False
    assert body["fields"] == ["client_id", "client_secret"]
    assert body["redirect_uri"].endswith("/api/integrations/oauth/google/callback")
    assert "super-secret" not in str(body)

    row = await _credential_row(workspace_ctx.id, "google")
    assert row is not None
    assert row.client_secret_encrypted != "super-secret"  # encrypted at rest


async def test_put_none_keeps_secret_empty_clears(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google", client_secret="keep-me")
    # Omitted secret → unchanged (still usable for connect).
    body = await put_credentials(workspace_ctx, "google", client_id="renamed", client_secret=None)
    assert body["has_secret"] is True
    await mint_authorize_url(workspace_ctx, "google")  # 200 → still configured

    # Empty string → cleared; connect stops working.
    body = await put_credentials(workspace_ctx, "google", client_secret="")
    assert body["has_secret"] is False
    response = await client.post(
        f"{workspace_ctx.base}/integrations/google/connect",
        json={},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 409


async def test_extra_fields_merge_and_delete(client, workspace_ctx):
    await put_credentials(workspace_ctx, "slack", extra={"signing_secret": "s1"})
    row = await _credential_row(workspace_ctx.id, "slack")
    assert row.extra == {"signing_secret": "s1"}

    await put_credentials(workspace_ctx, "slack")  # no extra sent → kept
    row = await _credential_row(workspace_ctx.id, "slack")
    assert row.extra == {"signing_secret": "s1"}

    await put_credentials(workspace_ctx, "slack", extra={"signing_secret": ""})  # "" deletes
    row = await _credential_row(workspace_ctx.id, "slack")
    assert row.extra == {}


async def test_extra_unknown_field_rejected(client, workspace_ctx):
    response = await client.put(
        f"{workspace_ctx.base}/integrations/google/credentials",
        json={"client_id": "x", "client_secret": "y", "extra": {"signing_secret": "nope"}},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 422


async def test_put_credentials_for_token_provider_rejected(client, workspace_ctx):
    response = await client.put(
        f"{workspace_ctx.base}/integrations/zendesk/credentials",
        json={"client_id": "x", "client_secret": "y"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 422


async def test_delete_credential_falls_back_to_env(client, workspace_ctx, monkeypatch):
    set_env_credentials(monkeypatch, "google", client_id="env-cid")
    await put_credentials(workspace_ctx, "google", client_id="row-cid")

    providers = await _providers_by_id(client, workspace_ctx)
    assert providers["google"]["credential"]["client_id"] == "row-cid"

    response = await client.delete(
        f"{workspace_ctx.base}/integrations/google/credentials",
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 200

    providers = await _providers_by_id(client, workspace_ctx)
    google = providers["google"]
    assert google["credential"]["client_id"] == "env-cid"
    assert google["credential"]["from_env"] is True
    assert google["configured"] is True


async def test_delete_missing_credential_404(client, workspace_ctx):
    response = await client.delete(
        f"{workspace_ctx.base}/integrations/google/credentials",
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 404


async def test_listing_needs_setup_and_zendesk_shape(client, workspace_ctx):
    providers = await _providers_by_id(client, workspace_ctx)
    assert set(providers) == {"google", "microsoft", "slack", "notion", "confluence", "zendesk"}

    google = providers["google"]
    assert google["configured"] is False
    assert google["connections"] == []
    assert google["credential"]["client_id"] is None
    assert google["credential"]["has_secret"] is False
    assert google["credential"]["redirect_uri"].endswith("/oauth/google/callback")

    slack = providers["slack"]
    assert slack["credential"]["fields"] == ["client_id", "client_secret", "signing_secret"]

    zendesk = providers["zendesk"]
    assert zendesk["auth"] == "token"
    assert zendesk["credential"] is None
    assert zendesk["configured"] is True  # nothing to set up on this page


async def test_listing_never_leaks_tokens_or_secrets(client, workspace_ctx, session):
    from tests.integrations.conftest import make_connection

    await put_credentials(workspace_ctx, "google", client_secret="super-secret-value")
    await make_connection(
        session, workspace_ctx.id, "google", access="raw-access-token", refresh="raw-refresh"
    )
    response = await client.get(
        f"{workspace_ctx.base}/integrations", headers=workspace_ctx.owner_headers
    )
    text = response.text
    for needle in ("super-secret-value", "raw-access-token", "raw-refresh", "_encrypted"):
        assert needle not in text

    providers = {p["id"]: p for p in response.json()["providers"]}
    (connection_out,) = providers["google"]["connections"]
    assert connection_out["account_label"] == "someone@example.com"
    assert connection_out["status"] == "connected"
