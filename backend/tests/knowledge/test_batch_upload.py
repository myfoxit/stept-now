"""Batch document upload: POST /knowledge/sources/{id}/documents/batch."""

from __future__ import annotations

from tests.conftest import bearer, drain_tasks, signup
from tests.knowledge.conftest import create_source, get_doc, list_docs

GOOD_MD = b"# Refund policy\n\nWe refund every purchase within 30 days."
GOOD_TXT = b"Support hours are 9am to 6pm on weekdays."


async def upload_batch(client, ctx, source_id, files, *, headers=None):
    return await client.post(
        f"{ctx.base}/knowledge/sources/{source_id}/documents/batch",
        files=files,
        headers=headers or ctx.owner_headers,
    )


async def test_batch_upload_mixes_good_and_bad_files(client, workspace_ctx):
    source = await create_source(client, workspace_ctx, type="files", name="Uploads")
    response = await upload_batch(
        client,
        workspace_ctx,
        source["id"],
        [
            ("file", ("policy.md", GOOD_MD, "text/markdown")),
            ("file", ("data.bin", b"\x00\x01\x02", "application/octet-stream")),
            ("file", ("hours.txt", GOOD_TXT, "text/plain")),
            ("file", ("empty.md", b"", "text/markdown")),
        ],
    )
    assert response.status_code == 201, response.text
    documents = response.json()
    assert len(documents) == 4  # order preserved, one row per submitted file

    by_title = {d["title"]: d for d in documents}
    assert by_title["Refund policy"]["status"] == "pending"
    assert by_title["hours"]["status"] == "pending"
    assert by_title["data.bin"]["status"] == "failed"
    assert "Unsupported file type" in by_title["data.bin"]["error"]
    assert by_title["data.bin"]["uri"] is None  # nothing was stored
    assert by_title["empty.md"]["status"] == "failed"
    assert by_title["empty.md"]["error"] == "Empty file"

    await drain_tasks()
    docs = await list_docs(client, workspace_ctx, source["id"])
    statuses = {d["title"]: d["status"] for d in docs}
    assert statuses["Refund policy"] == "indexed"
    assert statuses["hours"] == "indexed"
    assert statuses["data.bin"] == "failed"  # failures survive the sweep, not retried away


async def test_batch_upload_single_file_still_returns_a_list(client, workspace_ctx):
    source = await create_source(client, workspace_ctx, type="files")
    response = await upload_batch(
        client, workspace_ctx, source["id"], [("file", ("policy.md", GOOD_MD, "text/markdown"))]
    )
    assert response.status_code == 201
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    await drain_tasks()
    assert (await get_doc(client, workspace_ctx, body[0]["id"]))["status"] == "indexed"


async def test_batch_upload_rejects_empty_and_oversized_batches(client, workspace_ctx):
    source = await create_source(client, workspace_ctx, type="files")
    no_files = await client.post(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}/documents/batch",
        files={"other": ("policy.md", GOOD_MD, "text/markdown")},
        headers=workspace_ctx.owner_headers,
    )
    assert no_files.status_code == 400

    not_multipart = await client.post(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}/documents/batch",
        json={"title": "x", "content": "y"},
        headers=workspace_ctx.owner_headers,
    )
    assert not_multipart.status_code == 400

    too_many = await upload_batch(
        client,
        workspace_ctx,
        source["id"],
        [("file", (f"doc{i}.md", GOOD_MD, "text/markdown")) for i in range(21)],
    )
    assert too_many.status_code == 400
    assert "20 files" in too_many.json()["error"]["message"]
    assert await list_docs(client, workspace_ctx, source["id"]) == []  # nothing partially stored


async def test_batch_upload_into_articles_source_rejected(client, workspace_ctx):
    article = await client.post(
        f"{workspace_ctx.base}/articles",
        json={"title": "Some article", "body": "Text."},
        headers=workspace_ctx.owner_headers,
    )
    await client.post(
        f"{workspace_ctx.base}/articles/{article.json()['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    sources = (
        await client.get(
            f"{workspace_ctx.base}/knowledge/sources", headers=workspace_ctx.owner_headers
        )
    ).json()
    articles_source = next(s for s in sources if s["type"] == "articles")
    denied = await upload_batch(
        client,
        workspace_ctx,
        articles_source["id"],
        [("file", ("policy.md", GOOD_MD, "text/markdown"))],
    )
    assert denied.status_code == 400


async def test_batch_upload_authz_and_isolation(client, workspace_ctx):
    source = await create_source(client, workspace_ctx, type="files")
    viewer = await workspace_ctx.add_member("viewer@example.com", role="viewer")
    denied = await upload_batch(
        client,
        workspace_ctx,
        source["id"],
        [("file", ("policy.md", GOOD_MD, "text/markdown"))],
        headers=viewer,
    )
    assert denied.status_code == 403

    other_auth = await signup(client, "spy@example.com")
    other_ws = (
        await client.post("/api/v1/workspaces", json={"name": "Spy Co"}, headers=bearer(other_auth))
    ).json()
    cross = await client.post(
        f"/api/v1/w/{other_ws['id']}/knowledge/sources/{source['id']}/documents/batch",
        files=[("file", ("policy.md", GOOD_MD, "text/markdown"))],
        headers=bearer(other_auth),
    )
    assert cross.status_code == 404
