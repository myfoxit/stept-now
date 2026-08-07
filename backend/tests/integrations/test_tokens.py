"""The tokens seam: refresh skew, single-flight, rotation, failure semantics."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.core.db import get_session_factory
from app.core.errors import NotFoundError
from app.core.events import EventNames
from app.core.security import decrypt_secret
from app.integrations import tokens
from app.integrations.oauth import IntegrationAuthError
from tests.integrations.conftest import (
    CONFLUENCE_TOKEN_URL,
    GOOGLE_TOKEN_URL,
    NOTION_TOKEN_URL,
    capture_events,
    make_connection,
    put_credentials,
    set_env_credentials,
)


async def test_get_connection_scoped_to_workspace(client, workspace_ctx, session):
    connection = await make_connection(session, workspace_ctx.id)
    loaded = await tokens.get_connection(session, workspace_ctx.id, connection.id)
    assert loaded.id == connection.id

    with pytest.raises(NotFoundError):
        await tokens.get_connection(session, "other-workspace", connection.id)
    with pytest.raises(NotFoundError):
        await tokens.get_connection(session, workspace_ctx.id, "missing-id")


async def test_get_connection_refuses_revoked(client, workspace_ctx, session):
    connection = await make_connection(session, workspace_ctx.id, status="revoked")
    with pytest.raises(IntegrationAuthError):
        await tokens.get_connection(session, workspace_ctx.id, connection.id)


async def test_fresh_token_returned_without_http(client, workspace_ctx, session):
    connection = await make_connection(
        session, workspace_ctx.id, access="live-token", expires_in=3600
    )
    with respx.mock:  # no routes: any HTTP would raise
        token = await tokens.get_valid_access_token(session, connection)
    assert token == "live-token"


async def test_non_expiring_token_returned_without_http(client, workspace_ctx, session):
    connection = await make_connection(
        session, workspace_ctx.id, "slack", access="xoxb-live", refresh=None, expires_in=None
    )
    with respx.mock:
        assert await tokens.get_valid_access_token(session, connection) == "xoxb-live"


async def test_token_inside_skew_window_is_refreshed(client, workspace_ctx, session, monkeypatch):
    """expires_at 100s out < the 300s skew → refresh happens now."""
    set_env_credentials(monkeypatch, "google")
    connection = await make_connection(
        session, workspace_ctx.id, "google", access="stale", refresh="refresh-raw", expires_in=100
    )
    with respx.mock:
        route = respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "fresh", "expires_in": 3600, "token_type": "Bearer"}
            )
        )
        token = await tokens.get_valid_access_token(session, connection)

    assert token == "fresh"
    sent = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert sent == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh-raw",
        "client_id": "env-client-id",
        "client_secret": "env-client-secret",
    }
    # Persisted through a commit — visible to a brand-new session.
    async with get_session_factory()() as fresh_session:
        row = await fresh_session.get(type(connection), connection.id)
        assert decrypt_secret(row.access_token_encrypted) == "fresh"
        assert decrypt_secret(row.refresh_token_encrypted) == "refresh-raw"  # unchanged


async def test_rotated_refresh_token_is_persisted(client, workspace_ctx, session, monkeypatch):
    """Atlassian rotates the refresh token on every refresh — losing it loses the grant."""
    set_env_credentials(monkeypatch, "confluence")
    connection = await make_connection(
        session, workspace_ctx.id, "confluence", access="old", refresh="rot-1", expires_in=10
    )
    with respx.mock:
        respx.post(CONFLUENCE_TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "new-access", "refresh_token": "rot-2", "expires_in": 3600},
            )
        )
        assert await tokens.get_valid_access_token(session, connection) == "new-access"

    async with get_session_factory()() as fresh_session:
        row = await fresh_session.get(type(connection), connection.id)
        assert decrypt_secret(row.refresh_token_encrypted) == "rot-2"


async def test_notion_refresh_uses_basic_auth(client, workspace_ctx, session, monkeypatch):
    set_env_credentials(monkeypatch, "notion", client_id="ncid", client_secret="nsec")
    connection = await make_connection(
        session, workspace_ctx.id, "notion", access="old", refresh="n-rot-1", expires_in=10
    )
    with respx.mock:
        route = respx.post(NOTION_TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "n-new", "refresh_token": "n-rot-2"}
            )
        )
        assert await tokens.get_valid_access_token(session, connection) == "n-new"
    assert route.calls.last.request.headers["authorization"].startswith("Basic ")


async def test_refresh_failure_flags_reauth_and_raises(client, workspace_ctx, session, monkeypatch):
    set_env_credentials(monkeypatch, "google")
    connection = await make_connection(
        session, workspace_ctx.id, "google", access="stale", refresh="dead", expires_in=10
    )
    with respx.mock, capture_events(EventNames.INTEGRATION_REAUTH_REQUIRED) as captured:
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(IntegrationAuthError):
            await tokens.get_valid_access_token(session, connection)

    assert [event.payload["connection_id"] for event in captured] == [connection.id]
    async with get_session_factory()() as fresh_session:
        row = await fresh_session.get(type(connection), connection.id)
        assert row.status == "reauth_required"


async def test_missing_refresh_token_flags_reauth_without_http(client, workspace_ctx, session):
    connection = await make_connection(
        session, workspace_ctx.id, "google", access="stale", refresh=None, expires_in=10
    )
    with respx.mock, capture_events(EventNames.INTEGRATION_REAUTH_REQUIRED) as captured:
        with pytest.raises(IntegrationAuthError):
            await tokens.get_valid_access_token(session, connection)
    assert len(captured) == 1


async def test_refresh_is_single_flight_per_connection(client, workspace_ctx, session, monkeypatch):
    set_env_credentials(monkeypatch, "google")
    connection = await make_connection(
        session, workspace_ctx.id, "google", access="stale", refresh="refresh-raw", expires_in=10
    )
    with respx.mock:
        route = respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
        )
        first, second = await asyncio.gather(
            tokens.get_valid_access_token(session, connection),
            tokens.get_valid_access_token(session, connection),
        )
    assert first == second == "fresh"
    assert route.call_count == 1  # the waiter reused the winner's refresh


async def test_workspace_credential_used_for_refresh(client, workspace_ctx, session, monkeypatch):
    """Workspace row beats env for the refresh call too."""
    set_env_credentials(monkeypatch, "google", client_id="env-cid")
    await put_credentials(workspace_ctx, "google", client_id="row-cid", client_secret="row-sec")
    connection = await make_connection(
        session, workspace_ctx.id, "google", access="stale", refresh="r", expires_in=10
    )
    with respx.mock:
        route = respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "fresh", "expires_in": 60})
        )
        await tokens.get_valid_access_token(session, connection)
    sent = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert sent["client_id"] == "row-cid"
    assert sent["client_secret"] == "row-sec"
