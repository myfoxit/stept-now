"""Recorder flow: mint token (app) → create draft tour (public) with authz paths."""

from __future__ import annotations

from app.core.db import uuid7
from app.core.security import create_recorder_token, create_widget_token
from tests.tours.conftest import expired_recorder_token, user_id_from_headers


async def _mint(client, workspace_ctx) -> str:
    resp = await client.post(
        f"{workspace_ctx.base}/tours/recorder-token", headers=workspace_ctx.owner_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expires_days"] == 7
    return body["token"]


async def test_recorder_token_requires_manage(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("rec-viewer@example.com", role="viewer")
    denied = await client.post(f"{workspace_ctx.base}/tours/recorder-token", headers=viewer)
    assert denied.status_code == 403


async def test_recorder_creates_draft_tour(client, workspace_ctx):
    token = await _mint(client, workspace_ctx)
    resp = await client.post(
        "/api/widget/tours/recorder",
        json={
            "token": token,
            "name": "Recorded flow",
            "url_pattern": "*/settings*",
            "steps": [{"selector": "#a"}, {"selector": "#b", "title": "Second"}],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Recorded flow"
    assert body["app_url"].endswith(f"/tours/{body['id']}")

    # The draft is visible in the dashboard, version 1, default titles filled in.
    got = await client.get(
        f"{workspace_ctx.base}/tours/{body['id']}", headers=workspace_ctx.owner_headers
    )
    assert got.status_code == 200
    tour = got.json()
    assert tour["status"] == "draft"
    assert tour["version"] == 1
    assert tour["trigger"] == {"type": "url_match", "url_pattern": "*/settings*"}
    assert [s["title"] for s in tour["steps"]] == ["Step 1", "Second"]


async def test_recorder_without_url_pattern_is_manual(client, workspace_ctx):
    token = await _mint(client, workspace_ctx)
    resp = await client.post(
        "/api/widget/tours/recorder",
        json={"token": token, "name": "Manual", "steps": [{"selector": "#x"}]},
    )
    assert resp.status_code == 201
    got = await client.get(
        f"{workspace_ctx.base}/tours/{resp.json()['id']}", headers=workspace_ctx.owner_headers
    )
    assert got.json()["trigger"] == {"type": "manual", "url_pattern": None}


async def test_recorder_bad_token_401(client, workspace_ctx):
    resp = await client.post(
        "/api/widget/tours/recorder",
        json={"token": "not-a-real-token", "name": "X", "steps": [{"selector": "#a"}]},
    )
    assert resp.status_code == 401


async def test_recorder_wrong_token_type_401(client, workspace_ctx):
    # A widget token is the wrong typ for the recorder endpoint.
    widget_token = create_widget_token(workspace_ctx.id, uuid7())
    resp = await client.post(
        "/api/widget/tours/recorder",
        json={"token": widget_token, "name": "X", "steps": [{"selector": "#a"}]},
    )
    assert resp.status_code == 401


async def test_recorder_expired_token_401(client, workspace_ctx):
    user_id = user_id_from_headers(workspace_ctx.owner_headers)
    resp = await client.post(
        "/api/widget/tours/recorder",
        json={
            "token": expired_recorder_token(workspace_ctx.id, user_id),
            "name": "X",
            "steps": [{"selector": "#a"}],
        },
    )
    assert resp.status_code == 401


async def test_recorder_non_member_403(client, workspace_ctx):
    # Valid signature, but the subject is not a member of the workspace.
    token = create_recorder_token(workspace_ctx.id, uuid7())
    resp = await client.post(
        "/api/widget/tours/recorder",
        json={"token": token, "name": "X", "steps": [{"selector": "#a"}]},
    )
    assert resp.status_code == 403


async def test_recorder_member_without_perm_403(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("rec-noperm@example.com", role="viewer")
    viewer_id = user_id_from_headers(viewer)
    # Mint directly for the viewer (the app endpoint would refuse to); the public
    # endpoint must re-validate the permission and reject.
    token = create_recorder_token(workspace_ctx.id, viewer_id)
    resp = await client.post(
        "/api/widget/tours/recorder",
        json={"token": token, "name": "X", "steps": [{"selector": "#a"}]},
    )
    assert resp.status_code == 403
