"""AuthZ + disconnect: integrations:manage gate, cross-workspace 404s, revoke."""

from __future__ import annotations

import httpx
import respx
from sqlalchemy import select

from app.core.db import get_session_factory
from app.core.events import EventNames
from app.integrations.oauth import GOOGLE_REVOKE_URL
from app.models.audit import AuditLog
from tests.conftest import bearer, signup
from tests.integrations.conftest import capture_events, fetch_connections, make_connection


async def test_agent_role_is_403_everywhere(client, workspace_ctx):
    agent_headers = await workspace_ctx.add_member("agent@example.com", role="agent")
    base = workspace_ctx.base
    calls = [
        client.get(f"{base}/integrations", headers=agent_headers),
        client.post(f"{base}/integrations/google/connect", json={}, headers=agent_headers),
        client.post(
            f"{base}/integrations/connections/some-id/reconnect", json={}, headers=agent_headers
        ),
        client.delete(f"{base}/integrations/connections/some-id", headers=agent_headers),
        client.put(
            f"{base}/integrations/google/credentials",
            json={"client_id": "x", "client_secret": "y"},
            headers=agent_headers,
        ),
        client.delete(f"{base}/integrations/google/credentials", headers=agent_headers),
    ]
    for call in calls:
        response = await call
        assert response.status_code == 403, response.text


async def test_admin_role_allowed(client, workspace_ctx):
    admin_headers = await workspace_ctx.add_member("admin@example.com", role="admin")
    response = await client.get(f"{workspace_ctx.base}/integrations", headers=admin_headers)
    assert response.status_code == 200


async def test_cross_workspace_connection_is_404(client, workspace_ctx, session):
    """A connection id from another workspace must look nonexistent, not forbidden."""
    other_owner = await signup(client, "other-owner@example.com")
    other_ws = await client.post(
        "/api/v1/workspaces", json={"name": "Other Co"}, headers=bearer(other_owner)
    )
    foreign = await make_connection(session, other_ws.json()["id"], "google")

    for response in (
        await client.delete(
            f"{workspace_ctx.base}/integrations/connections/{foreign.id}",
            headers=workspace_ctx.owner_headers,
        ),
        await client.post(
            f"{workspace_ctx.base}/integrations/connections/{foreign.id}/reconnect",
            json={},
            headers=workspace_ctx.owner_headers,
        ),
    ):
        assert response.status_code == 404, response.text


async def test_unauthenticated_401(client, workspace_ctx):
    response = await client.get(f"{workspace_ctx.base}/integrations")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


async def test_disconnect_google_revokes_and_tombstones(client, workspace_ctx, session):
    connection = await make_connection(
        session, workspace_ctx.id, "google", access="acc", refresh="ref"
    )
    with respx.mock, capture_events(EventNames.INTEGRATION_DISCONNECTED) as captured:
        revoke = respx.post(GOOGLE_REVOKE_URL).mock(return_value=httpx.Response(200))
        response = await client.delete(
            f"{workspace_ctx.base}/integrations/connections/{connection.id}",
            headers=workspace_ctx.owner_headers,
        )
    assert response.status_code == 200
    # Revoking the refresh token kills the whole google grant.
    assert dict(httpx.QueryParams(revoke.calls.last.request.content.decode())) == {"token": "ref"}
    assert len(captured) == 1

    (row,) = await fetch_connections(workspace_ctx.id)
    assert row.status == "revoked"
    assert row.access_token_encrypted is None
    assert row.refresh_token_encrypted is None

    # Revoked connections vanish from the listing.
    listing = await client.get(
        f"{workspace_ctx.base}/integrations", headers=workspace_ctx.owner_headers
    )
    providers = {p["id"]: p for p in listing.json()["providers"]}
    assert providers["google"]["connections"] == []

    async with get_session_factory()() as audit_session:
        entry = (
            await audit_session.execute(
                select(AuditLog).where(
                    AuditLog.workspace_id == workspace_ctx.id,
                    AuditLog.action == "integration.disconnect",
                )
            )
        ).scalar_one()
        assert entry.target_id == connection.id


async def test_disconnect_survives_failing_revoke_endpoint(client, workspace_ctx, session):
    connection = await make_connection(session, workspace_ctx.id, "google")
    with respx.mock:
        respx.post(GOOGLE_REVOKE_URL).mock(side_effect=httpx.ConnectError("down"))
        response = await client.delete(
            f"{workspace_ctx.base}/integrations/connections/{connection.id}",
            headers=workspace_ctx.owner_headers,
        )
    assert response.status_code == 200
    (row,) = await fetch_connections(workspace_ctx.id)
    assert row.status == "revoked"


async def test_disconnect_slack_skips_provider_revoke(client, workspace_ctx, session):
    """auth.revoke does not support bot tokens — no HTTP at all for slack."""
    connection = await make_connection(session, workspace_ctx.id, "slack", refresh=None)
    with respx.mock:  # no routes: any HTTP would fail the test
        response = await client.delete(
            f"{workspace_ctx.base}/integrations/connections/{connection.id}",
            headers=workspace_ctx.owner_headers,
        )
    assert response.status_code == 200


async def test_disconnect_is_idempotent(client, workspace_ctx, session):
    connection = await make_connection(session, workspace_ctx.id, "slack", refresh=None)
    for _ in range(2):
        response = await client.delete(
            f"{workspace_ctx.base}/integrations/connections/{connection.id}",
            headers=workspace_ctx.owner_headers,
        )
        assert response.status_code == 200
