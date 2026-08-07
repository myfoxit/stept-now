"""Reconnect: same round trip, but the callback updates the connection in place."""

from __future__ import annotations

import httpx
import respx

from app.core.security import decrypt_secret
from app.integrations.oauth import verify_state
from tests.integrations.conftest import (
    GOOGLE_TOKEN_URL,
    fetch_connections,
    google_token_response,
    mint_authorize_url,
    put_credentials,
    query_of,
    run_callback,
    state_of,
)


async def _connect_google(client, workspace_ctx, **token_over):
    state = state_of(await mint_authorize_url(workspace_ctx, "google"))
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response(**token_over))
        )
        response = await run_callback(client, "google", state=state)
    assert response.status_code == 302
    (connection,) = [c for c in await fetch_connections(workspace_ctx.id) if c.provider == "google"]
    return connection


async def test_reconnect_updates_connection_in_place(client, workspace_ctx):
    await put_credentials(workspace_ctx, "google")
    original = await _connect_google(client, workspace_ctx)

    response = await client.post(
        f"{workspace_ctx.base}/integrations/connections/{original.id}/reconnect",
        json={},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 200, response.text
    authorize_url = response.json()["authorize_url"]
    state = query_of(authorize_url)["state"]
    assert verify_state(state)["connection_id"] == original.id

    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json=google_token_response(
                    email="renewed@example.com",
                    access_token="ya29.renewed",
                    refresh_token="1//rotated",
                ),
            )
        )
        callback = await run_callback(client, "google", state=state, code="second-code")
    assert "connected=google" in callback.headers["location"]

    connections = await fetch_connections(workspace_ctx.id)
    assert len(connections) == 1  # updated, not duplicated
    updated = connections[0]
    assert updated.id == original.id
    assert updated.account_label == "renewed@example.com"
    assert decrypt_secret(updated.access_token_encrypted) == "ya29.renewed"
    assert decrypt_secret(updated.refresh_token_encrypted) == "1//rotated"
    assert updated.status == "connected"


async def test_reconnect_keeps_refresh_token_when_not_reissued(client, workspace_ctx):
    """Google only re-sends refresh tokens on consent; a missing one is kept."""
    await put_credentials(workspace_ctx, "google")
    original = await _connect_google(client, workspace_ctx, refresh_token="1//keep-me")

    response = await client.post(
        f"{workspace_ctx.base}/integrations/connections/{original.id}/reconnect",
        json={},
        headers=workspace_ctx.owner_headers,
    )
    state = query_of(response.json()["authorize_url"])["state"]

    token_response = google_token_response(access_token="ya29.new")
    token_response.pop("refresh_token")
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(return_value=httpx.Response(200, json=token_response))
        await run_callback(client, "google", state=state, code="second-code")

    (updated,) = await fetch_connections(workspace_ctx.id)
    assert decrypt_secret(updated.access_token_encrypted) == "ya29.new"
    assert decrypt_secret(updated.refresh_token_encrypted) == "1//keep-me"


async def test_reconnect_reactivates_reauth_required_connection(client, workspace_ctx, session):
    from tests.integrations.conftest import make_connection

    await put_credentials(workspace_ctx, "google")
    stale = await make_connection(
        session, workspace_ctx.id, "google", status="reauth_required", expires_in=-100
    )

    response = await client.post(
        f"{workspace_ctx.base}/integrations/connections/{stale.id}/reconnect",
        json={},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 200
    state = query_of(response.json()["authorize_url"])["state"]
    with respx.mock:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json=google_token_response())
        )
        await run_callback(client, "google", state=state)

    (updated,) = await fetch_connections(workspace_ctx.id)
    assert updated.id == stale.id
    assert updated.status == "connected"


async def test_reconnect_unknown_connection_404(client, workspace_ctx):
    response = await client.post(
        f"{workspace_ctx.base}/integrations/connections/nope/reconnect",
        json={},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 404
