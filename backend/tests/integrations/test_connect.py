"""Connect endpoint: authorize-URL construction, credential resolution, /start."""

from __future__ import annotations

from urllib.parse import urlsplit

from app.core.config import get_settings, reset_settings_cache
from app.integrations.oauth import verify_state
from tests.integrations.conftest import (
    mint_authorize_url,
    put_credentials,
    query_of,
    set_env_credentials,
    state_of,
)


async def test_google_authorize_url_construction(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google", client_id="google-cid")
    url = await mint_authorize_url(workspace_ctx, "google")

    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    params = query_of(url)
    assert params["response_type"] == "code"
    assert params["client_id"] == "google-cid"
    assert params["access_type"] == "offline"
    assert params["prompt"] == "consent"
    assert params["scope"] == (
        "https://mail.google.com/ https://www.googleapis.com/auth/drive.readonly openid email"
    )
    assert params["redirect_uri"] == (
        get_settings().public_base_url.rstrip("/") + "/api/integrations/oauth/google/callback"
    )
    # State round-trips and pins workspace + provider + initiator.
    claims = verify_state(params["state"])
    assert claims["ws"] == workspace_ctx.id
    assert claims["provider"] == "google"
    assert claims["sub"] == workspace_ctx.owner_auth["user"]["id"]
    assert claims["return_to"] == "/settings/integrations"


async def test_slack_scopes_are_csv(client, workspace_ctx):
    await put_credentials(workspace_ctx, "slack")
    params = query_of(await mint_authorize_url(workspace_ctx, "slack"))
    assert params["scope"].startswith("channels:history,channels:read,chat:write")
    assert " " not in params["scope"]


async def test_notion_authorize_has_owner_and_no_scope(client, workspace_ctx):
    await put_credentials(workspace_ctx, "notion")
    params = query_of(await mint_authorize_url(workspace_ctx, "notion"))
    assert params["owner"] == "user"
    assert "scope" not in params


async def test_confluence_authorize_has_audience(client, workspace_ctx):
    await put_credentials(workspace_ctx, "confluence")
    params = query_of(await mint_authorize_url(workspace_ctx, "confluence"))
    assert params["audience"] == "api.atlassian.com"
    assert params["prompt"] == "consent"
    assert "offline_access" in params["scope"]


async def test_connect_without_credential_409(client, workspace_ctx):
    response = await client.post(
        f"{workspace_ctx.base}/integrations/google/connect",
        json={},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "integration_not_configured"


async def test_connect_unknown_provider_404(client, workspace_ctx):
    response = await client.post(
        f"{workspace_ctx.base}/integrations/doesnotexist/connect",
        json={},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 404


async def test_connect_token_provider_422(client, workspace_ctx):
    response = await client.post(
        f"{workspace_ctx.base}/integrations/zendesk/connect",
        json={},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 422


async def test_return_to_must_be_in_app_path(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google")
    for bad in ("https://evil.example/phish", "//evil.example", "settings/integrations"):
        response = await client.post(
            f"{workspace_ctx.base}/integrations/google/connect",
            json={"return_to": bad},
            headers=workspace_ctx.owner_headers,
        )
        assert response.status_code == 422, bad


async def test_workspace_credential_beats_env(client, workspace_ctx, monkeypatch):
    set_env_credentials(monkeypatch, "google", client_id="env-cid")
    await put_credentials(workspace_ctx, "google", client_id="workspace-cid")
    params = query_of(await mint_authorize_url(workspace_ctx, "google"))
    assert params["client_id"] == "workspace-cid"


async def test_env_credential_used_when_no_workspace_row(client, workspace_ctx, monkeypatch):
    set_env_credentials(monkeypatch, "google", client_id="env-cid")
    params = query_of(await mint_authorize_url(workspace_ctx, "google"))
    assert params["client_id"] == "env-cid"


async def test_oauth_base_override_rewrites_authorize_host(client, workspace_ctx, monkeypatch):
    monkeypatch.setenv("STEPT_OAUTH_BASE_OVERRIDE", '{"google": "http://oauth-stub.local:9"}')
    reset_settings_cache()
    await put_credentials(workspace_ctx, "google")
    url = await mint_authorize_url(workspace_ctx, "google")
    assert url.startswith("http://oauth-stub.local:9/o/oauth2/v2/auth?")


async def test_start_rebuilds_the_authorize_redirect(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google", client_id="google-cid")
    state = state_of(await mint_authorize_url(workspace_ctx, "google"))

    response = await client.get(f"/api/integrations/oauth/google/start?state={state}")
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert query_of(location)["state"] == state
    assert query_of(location)["client_id"] == "google-cid"


async def test_start_with_invalid_state_redirects_to_app(client, workspace_ctx):
    response = await client.get("/api/integrations/oauth/google/start?state=broken")
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(get_settings().app_base_url)
    assert "error=invalid_state" in location
