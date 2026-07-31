"""Public media: screenshot upload → unauthenticated serve, namespace isolation,
traversal rejection, and the `?public=true` upload flag."""

from __future__ import annotations

from app.core.storage import get_storage
from tests.tours.conftest import extension_headers

# Smallest valid PNG (1x1, transparent).
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)


async def _upload_screenshot(
    client, headers, *, name="shot.png", data=PNG, content_type="image/png"
):
    return await client.post(
        "/api/widget/dap/screenshots",
        files={"file": (name, data, content_type)},
        headers=headers,
    )


async def test_screenshot_upload_then_public_serve(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    resp = await _upload_screenshot(client, headers)
    assert resp.status_code == 201, resp.text
    key = resp.json()["key"]
    assert key.startswith(f"public/{workspace_ctx.id}/")

    # No auth, cached, correct type.
    served = await client.get(f"/api/widget/media/{workspace_ctx.id}/{key}")
    assert served.status_code == 200, served.text
    assert served.content == PNG
    assert served.headers["content-type"].startswith("image/png")
    assert served.headers["cache-control"] == "public, max-age=86400"
    assert served.headers["x-content-type-options"] == "nosniff"


async def test_screenshot_requires_extension_auth(client, workspace_ctx):
    anonymous = await _upload_screenshot(client, {})
    assert anonymous.status_code == 401


async def test_screenshot_type_and_size_limits(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    wrong_type = await _upload_screenshot(
        client, headers, name="doc.pdf", data=b"%PDF-1.4", content_type="application/pdf"
    )
    assert wrong_type.status_code == 422

    empty = await _upload_screenshot(client, headers, data=b"")
    assert empty.status_code == 422

    too_big = await _upload_screenshot(client, headers, data=b"x" * (2 * 1024 * 1024 + 1))
    assert too_big.status_code == 413


async def test_public_media_is_workspace_isolated(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    key = (await _upload_screenshot(client, headers)).json()["key"]

    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=workspace_ctx.owner_headers
    )
    other_id = other.json()["id"]
    # The key belongs to workspace A; serving it under workspace B's path 404s.
    leaked = await client.get(f"/api/widget/media/{other_id}/{key}")
    assert leaked.status_code == 404


async def test_private_keys_are_not_publicly_servable(client, workspace_ctx):
    """A private attachment key must stay behind the authenticated files route."""
    upload = await client.post(
        f"{workspace_ctx.base}/files",
        files={"file": ("secret.png", PNG, "image/png")},
        headers=workspace_ctx.owner_headers,
    )
    assert upload.status_code == 201, upload.text
    private_key = upload.json()["key"]
    assert not private_key.startswith("public/")

    for candidate in (
        private_key,
        f"public/{workspace_ctx.id}/../../{private_key}",
    ):
        resp = await client.get(f"/api/widget/media/{workspace_ctx.id}/{candidate}")
        assert resp.status_code == 404, candidate


async def test_traversal_attempts_rejected(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    await _upload_screenshot(client, headers)
    for candidate in (
        "../../../../etc/passwd",
        f"public/{workspace_ctx.id}/../../../etc/passwd",
        "public/other-workspace/2026/07/x.png",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    ):
        resp = await client.get(f"/api/widget/media/{workspace_ctx.id}/{candidate}")
        assert resp.status_code == 404, candidate


async def test_missing_public_key_404s(client, workspace_ctx):
    resp = await client.get(
        f"/api/widget/media/{workspace_ctx.id}/public/{workspace_ctx.id}/2026/07/nope.png"
    )
    assert resp.status_code == 404


async def test_files_public_flag_stores_in_public_namespace(client, workspace_ctx):
    resp = await client.post(
        f"{workspace_ctx.base}/files?public=true",
        files={"file": ("step.png", PNG, "image/png")},
        headers=workspace_ctx.owner_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["key"].startswith(f"public/{workspace_ctx.id}/")
    assert body["url"] == f"/api/widget/media/{workspace_ctx.id}/{body['key']}"
    assert body["size"] == len(PNG)

    served = await client.get(body["url"])
    assert served.status_code == 200
    assert served.content == PNG


async def test_files_public_flag_restricts_content_types(client, workspace_ctx):
    denied = await client.post(
        f"{workspace_ctx.base}/files?public=true",
        files={"file": ("notes.pdf", b"%PDF-1.4", "application/pdf")},
        headers=workspace_ctx.owner_headers,
    )
    assert denied.status_code == 400
    # …while the private route still accepts PDFs exactly as before.
    allowed = await client.post(
        f"{workspace_ctx.base}/files",
        files={"file": ("notes.pdf", b"%PDF-1.4", "application/pdf")},
        headers=workspace_ctx.owner_headers,
    )
    assert allowed.status_code == 201
    assert allowed.json()["url"].startswith(f"/api/v1/w/{workspace_ctx.id}/files/")


async def test_public_upload_requires_membership(client, workspace_ctx):
    from tests.conftest import bearer, signup

    outsider = bearer(await signup(client, "media-outsider@example.com"))
    denied = await client.post(
        f"{workspace_ctx.base}/files?public=true",
        files={"file": ("step.png", PNG, "image/png")},
        headers=outsider,
    )
    assert denied.status_code == 403


async def test_svg_served_with_a_locked_down_csp(client, workspace_ctx):
    resp = await client.post(
        f"{workspace_ctx.base}/files?public=true",
        files={"file": ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")},
        headers=workspace_ctx.owner_headers,
    )
    assert resp.status_code == 201, resp.text
    served = await client.get(resp.json()["url"])
    assert served.status_code == 200
    assert "default-src 'none'" in served.headers["content-security-policy"]


async def test_public_key_layout_is_date_partitioned(client, workspace_ctx):
    headers = await extension_headers(client, workspace_ctx)
    key = (await _upload_screenshot(client, headers)).json()["key"]
    parts = key.split("/")
    assert parts[0] == "public"
    assert parts[1] == workspace_ctx.id
    assert len(parts[2]) == 4 and parts[2].isdigit()  # year
    assert len(parts[3]) == 2 and parts[3].isdigit()  # month
    # The bytes really are in storage under that exact key.
    assert await get_storage().read(key) == PNG
