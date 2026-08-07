"""Public callback: per-provider happy paths + every failure becomes a redirect."""

from __future__ import annotations

import base64
import json
from datetime import timedelta

import httpx
import respx

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.events import EventNames
from app.core.security import decrypt_secret
from app.integrations.oauth import ATLASSIAN_RESOURCES_URL
from tests.integrations.conftest import (
    CONFLUENCE_TOKEN_URL,
    GOOGLE_TOKEN_URL,
    MICROSOFT_TOKEN_URL,
    NOTION_TOKEN_URL,
    atlassian_site,
    capture_events,
    confluence_token_response,
    fetch_connections,
    google_token_response,
    microsoft_token_response,
    mint_authorize_url,
    notion_token_response,
    put_credentials,
    run_callback,
    state_of,
)


def _app_url(path_and_query: str) -> str:
    return get_settings().app_base_url.rstrip("/") + path_and_query


async def test_google_callback_creates_connection(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google")
    state = state_of(await mint_authorize_url(workspace_ctx, "google"))

    with respx.mock, capture_events(EventNames.INTEGRATION_CONNECTED) as captured:
        route = respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response())
        )
        response = await run_callback(client, "google", state=state)

    assert response.status_code == 302
    assert response.headers["location"] == _app_url("/settings/integrations?connected=google")

    sent = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "auth-code"
    assert sent["client_id"] == "ws-client-id"
    assert sent["redirect_uri"].endswith("/api/integrations/oauth/google/callback")

    (connection,) = await fetch_connections(workspace_ctx.id)
    assert connection.provider == "google"
    assert connection.category == "email"
    assert connection.status == "connected"
    assert connection.account_label == "gmail-user@example.com"
    assert decrypt_secret(connection.access_token_encrypted) == "ya29.google-access"
    assert decrypt_secret(connection.refresh_token_encrypted) == "1//google-refresh"
    assert connection.token_expires_at is not None
    assert connection.token_expires_at > utcnow() + timedelta(seconds=3000)
    assert connection.scopes == ["https://mail.google.com/", "openid", "email"]
    assert connection.created_by == workspace_ctx.owner_auth["user"]["id"]
    assert [event.payload["connection_id"] for event in captured] == [connection.id]


async def test_microsoft_callback_uses_upn_and_tenant(client, workspace_ctx):
    await put_credentials(workspace_ctx, "microsoft")
    state = state_of(await mint_authorize_url(workspace_ctx, "microsoft"))

    with respx.mock:
        respx.post(MICROSOFT_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=microsoft_token_response(upn="upn@contoso.com"))
        )
        response = await run_callback(client, "microsoft", state=state)

    assert response.status_code == 302
    (connection,) = await fetch_connections(workspace_ctx.id)
    assert connection.account_label == "upn@contoso.com"
    assert connection.meta["tenant"] == "tenant-123"


async def test_notion_callback_uses_basic_auth(client, workspace_ctx):
    await put_credentials(workspace_ctx, "notion", client_id="ncid", client_secret="nsec")
    state = state_of(await mint_authorize_url(workspace_ctx, "notion"))

    with respx.mock:
        route = respx.post(NOTION_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=notion_token_response())
        )
        response = await run_callback(client, "notion", state=state)

    assert response.status_code == 302
    request = route.calls.last.request
    expected = "Basic " + base64.b64encode(b"ncid:nsec").decode()
    assert request.headers["authorization"] == expected
    body = json.loads(request.content)
    assert body["grant_type"] == "authorization_code"
    assert "client_secret" not in body  # secret rides in the Basic header only

    (connection,) = await fetch_connections(workspace_ctx.id)
    assert connection.account_label == "Acme Notes"
    assert connection.meta == {"bot_id": "bot-abc", "workspace_name": "Acme Notes"}
    assert connection.token_expires_at is None  # Notion tokens do not expire


async def test_confluence_callback_single_site_pins_cloud_id(client, workspace_ctx):
    await put_credentials(workspace_ctx, "confluence")
    state = state_of(await mint_authorize_url(workspace_ctx, "confluence"))

    with respx.mock:
        respx.post(CONFLUENCE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=confluence_token_response())
        )
        whoami = respx.get(ATLASSIAN_RESOURCES_URL).mock(
            return_value=httpx.Response(200, json=[atlassian_site("cloud-77", "Acme Wiki")])
        )
        response = await run_callback(client, "confluence", state=state)

    assert response.status_code == 302
    assert whoami.calls.last.request.headers["authorization"] == "Bearer atl-access"
    (connection,) = await fetch_connections(workspace_ctx.id)
    assert connection.account_label == "Acme Wiki"
    assert connection.meta["cloud_id"] == "cloud-77"
    assert connection.meta["site_url"].endswith("atlassian.net")
    assert decrypt_secret(connection.refresh_token_encrypted) == "atl-refresh"


async def test_confluence_callback_multiple_sites_defers_choice(client, workspace_ctx):
    await put_credentials(workspace_ctx, "confluence")
    state = state_of(await mint_authorize_url(workspace_ctx, "confluence"))

    with respx.mock:
        respx.post(CONFLUENCE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=confluence_token_response())
        )
        respx.get(ATLASSIAN_RESOURCES_URL).mock(
            return_value=httpx.Response(
                200, json=[atlassian_site("c-1", "One"), atlassian_site("c-2", "Two")]
            )
        )
        await run_callback(client, "confluence", state=state)

    (connection,) = await fetch_connections(workspace_ctx.id)
    assert "cloud_id" not in connection.meta
    assert [site["cloud_id"] for site in connection.meta["sites"]] == ["c-1", "c-2"]


async def test_callback_carries_custom_return_to(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google")
    state = state_of(
        await mint_authorize_url(workspace_ctx, "google", return_to="/settings/channels")
    )
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response())
        )
        response = await run_callback(client, "google", state=state)
    assert response.headers["location"] == _app_url("/settings/channels?connected=google")


async def test_callback_user_denied(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google")
    state = state_of(await mint_authorize_url(workspace_ctx, "google"))
    response = await run_callback(client, "google", state=state, code=None, error="access_denied")
    assert response.status_code == 302
    assert response.headers["location"] == _app_url("/settings/integrations?error=access_denied")
    assert await fetch_connections(workspace_ctx.id) == []


async def test_callback_provider_error_is_sanitized(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google")
    state = state_of(await mint_authorize_url(workspace_ctx, "google"))
    response = await run_callback(
        client, "google", state=state, code=None, error="invalid_scope <script>"
    )
    assert response.headers["location"] == _app_url("/settings/integrations?error=provider_error")


async def test_callback_invalid_state(client, workspace_ctx):
    response = await run_callback(client, "google", state="tampered")
    assert response.status_code == 302
    assert response.headers["location"] == _app_url("/settings/integrations?error=invalid_state")


async def test_callback_missing_state(client, workspace_ctx):
    response = await client.get("/api/integrations/oauth/google/callback?code=x")
    assert response.status_code == 302
    assert "error=invalid_state" in response.headers["location"]


async def test_callback_state_provider_mismatch(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google")
    google_state = state_of(await mint_authorize_url(workspace_ctx, "google"))
    response = await run_callback(client, "slack", state=google_state)
    assert "error=invalid_state" in response.headers["location"]
    assert await fetch_connections(workspace_ctx.id) == []


async def test_callback_missing_code(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google")
    state = state_of(await mint_authorize_url(workspace_ctx, "google"))
    response = await run_callback(client, "google", state=state, code=None)
    assert "error=missing_code" in response.headers["location"]


async def test_callback_exchange_failure_never_500s(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google")
    state = state_of(await mint_authorize_url(workspace_ctx, "google"))
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        response = await run_callback(client, "google", state=state)
    assert response.status_code == 302
    assert response.headers["location"] == _app_url("/settings/integrations?error=connect_failed")
    assert await fetch_connections(workspace_ctx.id) == []


async def test_callback_slack_ok_false_treated_as_failure(client, workspace_ctx):
    await put_credentials(workspace_ctx, "slack")
    state = state_of(await mint_authorize_url(workspace_ctx, "slack"))
    with respx.mock:
        respx.post("https://slack.com/api/oauth.v2.access").mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "invalid_code"})
        )
        response = await run_callback(client, "slack", state=state)
    assert "error=connect_failed" in response.headers["location"]
    assert await fetch_connections(workspace_ctx.id) == []
