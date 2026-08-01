"""Sandbox DOM replicas: upload → public serve, with the guardrails that keep a
captured page from ever executing on a Stept origin."""

from __future__ import annotations

import json

from tests.tours.conftest import extension_headers

SNAPSHOT = {
    "html": "<html><body><h1>Dashboard</h1></body></html>",
    "css": [".x{color:red}"],
    "viewport": {"w": 1280, "h": 800},
    "url": "https://app.example.com/dashboard",
    "title": "Dashboard",
}


async def _upload(client, headers, *, payload=None, raw=None, name="snapshot.json"):
    data = raw if raw is not None else json.dumps(payload or SNAPSHOT).encode()
    return await client.post(
        "/api/widget/dap/snapshots",
        files={"file": (name, data, "application/json")},
        headers=headers,
    )


async def test_snapshot_upload_then_public_serve(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    resp = await _upload(client, headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    key = body["key"]
    assert key.startswith(f"public/{workspace_ctx.id}/")
    assert key.endswith(".json")
    assert body["bytes"] == len(json.dumps(SNAPSHOT).encode())

    served = await client.get(f"/api/widget/media/{workspace_ctx.id}/{key}")
    assert served.status_code == 200, served.text
    assert served.json() == SNAPSHOT


async def test_snapshot_is_never_served_as_html(client, workspace_ctx):
    """The replica contains the customer's own markup and inline styles. Served
    as text/html from our origin it would be a stored XSS on the dashboard; the
    player is expected to mount it in a sandboxed iframe instead."""
    headers = await extension_headers(client, workspace_ctx)
    key = (
        await _upload(
            client,
            headers,
            payload={**SNAPSHOT, "html": "<img src=x onerror=alert(document.domain)>"},
            name="evil.html",
        )
    ).json()["key"]

    served = await client.get(f"/api/widget/media/{workspace_ctx.id}/{key}")
    assert served.headers["content-type"].startswith("application/json")
    assert served.headers["x-content-type-options"] == "nosniff"
    assert key.endswith(".json"), "a .html filename must not survive into the key"


async def test_snapshot_requires_extension_auth(client, workspace_ctx):
    assert (await _upload(client, {})).status_code == 401


async def test_snapshot_rejects_non_json_and_bad_shape(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    assert (await _upload(client, headers, raw=b"")).status_code == 422
    assert (await _upload(client, headers, raw=b"<html></html>")).status_code == 422
    assert (await _upload(client, headers, raw=b'["not", "an", "object"]')).status_code == 422
    assert (await _upload(client, headers, payload={"css": []})).status_code == 422
    assert (await _upload(client, headers, payload={"html": 42})).status_code == 422


async def test_snapshot_size_cap(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    oversized = await _upload(client, headers, raw=b"x" * (8 * 1024 * 1024 + 1))
    assert oversized.status_code == 413


async def test_snapshots_are_workspace_isolated(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    key = (await _upload(client, headers)).json()["key"]

    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=workspace_ctx.owner_headers
    )
    leaked = await client.get(f"/api/widget/media/{other.json()['id']}/{key}")
    assert leaked.status_code == 404
