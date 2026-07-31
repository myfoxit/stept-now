"""Campaigns API: CRUD, type/inbox validation, activate/pause, authz."""

from __future__ import annotations

from sqlalchemy import update

from app.core.db import get_session_factory
from app.models.campaign import Campaign
from tests.campaigns.conftest import create_campaign_via_api, create_inbox_via_api
from tests.conftest import WorkspaceCtx


async def test_one_off_crud_flow(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    inbox = await create_inbox_via_api(client, ctx, channel_type="email")
    campaign = await create_campaign_via_api(client, ctx, inbox_id=inbox["id"])
    assert campaign["status"] == "draft"
    assert campaign["campaign_type"] == "one_off"
    assert campaign["scheduled_at"] is not None  # defaults to now for one_off
    assert campaign["sent_count"] == 0

    listed = await client.get(f"{ctx.base}/campaigns", headers=ctx.owner_headers)
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [campaign["id"]]

    patched = await client.patch(
        f"{ctx.base}/campaigns/{campaign['id']}",
        json={"title": "Renamed", "message": "Updated {{contact.name}}"},
        headers=ctx.owner_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"

    deleted = await client.delete(
        f"{ctx.base}/campaigns/{campaign['id']}", headers=ctx.owner_headers
    )
    assert deleted.status_code == 200
    assert (await client.get(f"{ctx.base}/campaigns", headers=ctx.owner_headers)).json() == []


async def test_ongoing_requires_widget_inbox(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    email_inbox = await create_inbox_via_api(client, ctx, channel_type="email")
    response = await client.post(
        f"{ctx.base}/campaigns",
        json={
            "title": "Nope",
            "message": "Hi!",
            "campaign_type": "ongoing",
            "inbox_id": email_inbox["id"],
        },
        headers=ctx.owner_headers,
    )
    assert response.status_code == 422
    assert "widget" in response.json()["error"]["message"]


async def test_one_off_requires_messaging_inbox(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    widget_inbox = await create_inbox_via_api(client, ctx, channel_type="widget")
    response = await client.post(
        f"{ctx.base}/campaigns",
        json={
            "title": "Nope",
            "message": "Hi!",
            "campaign_type": "one_off",
            "inbox_id": widget_inbox["id"],
        },
        headers=ctx.owner_headers,
    )
    assert response.status_code == 422


async def test_activate_requires_trigger_rules_then_pause(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    widget_inbox = await create_inbox_via_api(client, ctx, channel_type="widget")
    campaign = await create_campaign_via_api(
        client, ctx, inbox_id=widget_inbox["id"], campaign_type="ongoing"
    )
    assert campaign["scheduled_at"] is None  # ongoing campaigns are never scheduled

    denied = await client.post(
        f"{ctx.base}/campaigns/{campaign['id']}/activate", headers=ctx.owner_headers
    )
    assert denied.status_code == 422  # no trigger rules yet

    await client.patch(
        f"{ctx.base}/campaigns/{campaign['id']}",
        json={"trigger_rules": {"url_pattern": "/pricing*", "time_on_page_seconds": 10}},
        headers=ctx.owner_headers,
    )
    activated = await client.post(
        f"{ctx.base}/campaigns/{campaign['id']}/activate", headers=ctx.owner_headers
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"

    # Activating a non-draft campaign conflicts.
    again = await client.post(
        f"{ctx.base}/campaigns/{campaign['id']}/activate", headers=ctx.owner_headers
    )
    assert again.status_code == 409

    paused = await client.post(
        f"{ctx.base}/campaigns/{campaign['id']}/pause", headers=ctx.owner_headers
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "draft"


async def test_agent_cannot_manage_campaigns(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    agent = await ctx.add_member("agent@example.com")
    inbox = await create_inbox_via_api(client, ctx, channel_type="email")
    response = await client.post(
        f"{ctx.base}/campaigns",
        json={
            "title": "Nope",
            "message": "Hi!",
            "campaign_type": "one_off",
            "inbox_id": inbox["id"],
        },
        headers=agent,
    )
    assert response.status_code == 403  # automations:manage required
    assert (await client.get(f"{ctx.base}/campaigns", headers=agent)).status_code == 200


async def test_patch_blocked_while_processing(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    inbox = await create_inbox_via_api(client, ctx, channel_type="email")
    campaign = await create_campaign_via_api(client, ctx, inbox_id=inbox["id"])
    async with get_session_factory()() as session:
        await session.execute(
            update(Campaign).where(Campaign.id == campaign["id"]).values(status="processing")
        )
        await session.commit()

    response = await client.patch(
        f"{ctx.base}/campaigns/{campaign['id']}",
        json={"title": "Too late"},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 409


async def test_cross_workspace_campaign_404(client, workspace_ctx: WorkspaceCtx):
    ctx = workspace_ctx
    inbox = await create_inbox_via_api(client, ctx, channel_type="email")
    campaign = await create_campaign_via_api(client, ctx, inbox_id=inbox["id"])
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=ctx.owner_headers
    )
    other_base = f"/api/v1/w/{other.json()['id']}"
    response = await client.get(
        f"{other_base}/campaigns/{campaign['id']}", headers=ctx.owner_headers
    )
    assert response.status_code == 404
