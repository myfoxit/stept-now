"""API keys (create/scopes/revoke, key auth) and file upload/serving."""


async def test_api_key_lifecycle_and_auth(client, workspace_ctx):
    create = await client.post(
        f"{workspace_ctx.base}/api-keys",
        json={"name": "CI key", "scopes": ["read"]},
        headers=workspace_ctx.owner_headers,
    )
    assert create.status_code == 201, create.text
    body = create.json()
    full_key = body["key"]
    assert full_key.startswith("sk_stept_")

    # Listing never returns the secret again.
    listing = await client.get(
        f"{workspace_ctx.base}/api-keys", headers=workspace_ctx.owner_headers
    )
    assert "key" not in listing.json()[0]

    key_headers = {"Authorization": f"Bearer {full_key}"}
    # read scope: can read workspace + members
    ws = await client.get(workspace_ctx.base, headers=key_headers)
    assert ws.status_code == 200
    members = await client.get(f"{workspace_ctx.base}/members", headers=key_headers)
    assert members.status_code == 200
    # but cannot manage members (write-level perms absent)
    patch = await client.patch(
        f"{workspace_ctx.base}/members/{members.json()[0]['id']}",
        json={"is_available": False},
        headers=key_headers,
    )
    assert patch.status_code == 403
    # API keys cannot hit user endpoints
    me = await client.get("/api/v1/me", headers=key_headers)
    assert me.status_code == 401

    revoke = await client.delete(
        f"{workspace_ctx.base}/api-keys/{body['id']}", headers=workspace_ctx.owner_headers
    )
    assert revoke.status_code == 200
    after = await client.get(workspace_ctx.base, headers=key_headers)
    assert after.status_code == 401


async def test_api_key_scope_validation(client, workspace_ctx):
    response = await client.post(
        f"{workspace_ctx.base}/api-keys",
        json={"name": "bad", "scopes": ["superpowers"]},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 400


async def test_api_key_workspace_isolation(client, workspace_ctx):
    from tests.conftest import bearer, signup

    other_auth = await signup(client, "other-owner@example.com")
    other_ws = (
        await client.post(
            "/api/v1/workspaces", json={"name": "Other Co"}, headers=bearer(other_auth)
        )
    ).json()

    create = await client.post(
        f"{workspace_ctx.base}/api-keys",
        json={"name": "mine", "scopes": ["admin"]},
        headers=workspace_ctx.owner_headers,
    )
    full_key = create.json()["key"]
    cross = await client.get(
        f"/api/v1/w/{other_ws['id']}", headers={"Authorization": f"Bearer {full_key}"}
    )
    assert cross.status_code == 403


async def test_file_upload_and_serving(client, workspace_ctx):
    upload = await client.post(
        f"{workspace_ctx.base}/files",
        files={"file": ("hello.txt", b"hello stept", "text/plain")},
        headers=workspace_ctx.owner_headers,
    )
    assert upload.status_code == 201, upload.text
    meta = upload.json()
    assert meta["size"] == 11

    served = await client.get(
        f"{workspace_ctx.base}/files/{meta['key']}", headers=workspace_ctx.owner_headers
    )
    assert served.status_code == 200
    assert served.content == b"hello stept"

    # Disallowed type
    bad = await client.post(
        f"{workspace_ctx.base}/files",
        files={"file": ("evil.exe", b"MZ", "application/x-msdownload")},
        headers=workspace_ctx.owner_headers,
    )
    assert bad.status_code == 400

    # Unauthenticated access is rejected
    anonymous = await client.get(f"{workspace_ctx.base}/files/{meta['key']}")
    assert anonymous.status_code == 401
