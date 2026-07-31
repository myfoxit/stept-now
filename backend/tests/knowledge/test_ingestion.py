"""Ingestion pipeline: upload → task → indexed; hash skip; URL sync; failures."""

from __future__ import annotations

import httpx
import respx
from sqlalchemy import select

from tests.conftest import drain_tasks
from tests.knowledge.conftest import create_source, paste_text

MARKDOWN = """# Onboarding checklist

Welcome to Stept! Install the widget first.

## Invite your team

Add teammates from the members page and give them roles.

## Connect knowledge

Upload your docs so the AI agent can answer with citations.
"""


async def test_file_upload_ingests_end_to_end(client, workspace_ctx):
    source = await create_source(client, workspace_ctx, type="files", name="Uploads")
    upload = await client.post(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}/documents",
        files={"file": ("onboarding.md", MARKDOWN.encode(), "text/markdown")},
        headers=workspace_ctx.owner_headers,
    )
    assert upload.status_code == 201, upload.text
    document = upload.json()
    assert document["status"] == "pending"
    assert document["title"] == "Onboarding checklist"

    await drain_tasks()

    detail = await client.get(
        f"{workspace_ctx.base}/knowledge/documents/{document['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "indexed"
    assert body["token_count"] > 0
    assert body["content_hash"]
    assert body["chunks"], "chunk preview should not be empty"
    assert body["chunks"][0]["content"].startswith("# Onboarding checklist")

    # source document_count reflects the upload
    listed = await client.get(
        f"{workspace_ctx.base}/knowledge/sources", headers=workspace_ctx.owner_headers
    )
    assert [s["document_count"] for s in listed.json() if s["id"] == source["id"]] == [1]


async def test_text_paste_ingests(client, workspace_ctx):
    source = await create_source(client, workspace_ctx)
    document = await paste_text(
        client,
        workspace_ctx,
        source["id"],
        title="Refund policy",
        content="We refund every purchase within 30 days, no questions asked.",
    )
    await drain_tasks()
    detail = await client.get(
        f"{workspace_ctx.base}/knowledge/documents/{document['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert detail.json()["status"] == "indexed"
    assert detail.json()["mime"] == "text/markdown"


async def test_reingest_unchanged_keeps_chunk_ids(client, workspace_ctx):
    source = await create_source(client, workspace_ctx)
    document = await paste_text(
        client, workspace_ctx, source["id"], title="Stable doc", content=MARKDOWN
    )
    await drain_tasks()

    async def chunk_ids() -> list[str]:
        detail = await client.get(
            f"{workspace_ctx.base}/knowledge/documents/{document['id']}",
            headers=workspace_ctx.owner_headers,
        )
        assert detail.json()["status"] == "indexed"
        return [chunk["id"] for chunk in detail.json()["chunks"]]

    first = await chunk_ids()
    assert first

    retry = await client.post(
        f"{workspace_ctx.base}/knowledge/documents/{document['id']}/retry",
        headers=workspace_ctx.owner_headers,
    )
    assert retry.status_code == 200
    await drain_tasks()
    assert await chunk_ids() == first  # unchanged content → chunk ids stable


async def test_unparseable_upload_returns_400(client, workspace_ctx):
    source = await create_source(client, workspace_ctx, type="files")
    bad_type = await client.post(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}/documents",
        files={"file": ("data.bin", b"\x00\x01", "application/octet-stream")},
        headers=workspace_ctx.owner_headers,
    )
    assert bad_type.status_code == 400
    corrupt_pdf = await client.post(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}/documents",
        files={"file": ("broken.pdf", b"%PDF-not-a-pdf", "application/pdf")},
        headers=workspace_ctx.owner_headers,
    )
    assert corrupt_pdf.status_code == 400
    empty = await client.post(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}/documents",
        files={"file": ("empty.md", b"", "text/markdown")},
        headers=workspace_ctx.owner_headers,
    )
    assert empty.status_code == 400


async def test_retry_after_storage_loss_marks_failed(client, workspace_ctx):
    from app.core.db import get_session_factory
    from app.models.knowledge import Document

    source = await create_source(client, workspace_ctx)
    document = await paste_text(
        client, workspace_ctx, source["id"], title="Doomed", content="Some content here."
    )
    await drain_tasks()

    async with get_session_factory()() as session:
        row = (
            await session.execute(select(Document).where(Document.id == document["id"]))
        ).scalar_one()
        row.uri = "2099/01/definitely-missing.md"
        row.content_hash = None
        await session.commit()

    retry = await client.post(
        f"{workspace_ctx.base}/knowledge/documents/{document['id']}/retry",
        headers=workspace_ctx.owner_headers,
    )
    assert retry.status_code == 200
    await drain_tasks()

    detail = await client.get(
        f"{workspace_ctx.base}/knowledge/documents/{document['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert detail.json()["status"] == "failed"
    assert detail.json()["error"]


HTML_PAGE = b"""<html><head><title>Pricing page</title></head><body>
<h1>Pricing</h1><p>Pro costs 49 dollars per seat per month.</p></body></html>"""


async def test_sync_urls_source_creates_and_indexes_documents(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="urls",
        name="Website",
        config={"urls": ["https://docs.example.com/pricing", "https://docs.example.com/faq"]},
    )
    with respx.mock:
        respx.get("https://docs.example.com/pricing").mock(
            return_value=httpx.Response(
                200, content=HTML_PAGE, headers={"content-type": "text/html"}
            )
        )
        respx.get("https://docs.example.com/faq").mock(
            return_value=httpx.Response(
                200,
                content=(
                    b"<html><head><title>FAQ</title></head>"
                    b"<body><p>Answers live here.</p></body></html>"
                ),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        )
        sync = await client.post(
            f"{workspace_ctx.base}/knowledge/sources/{source['id']}/sync",
            headers=workspace_ctx.owner_headers,
        )
        assert sync.status_code == 200
        assert sync.json()["status"] == "syncing"
        await drain_tasks()

    documents = await client.get(
        f"{workspace_ctx.base}/knowledge/documents?source_id={source['id']}",
        headers=workspace_ctx.owner_headers,
    )
    body = documents.json()
    assert body["total"] == 2
    by_uri = {d["uri"]: d for d in body["items"]}
    assert by_uri["https://docs.example.com/pricing"]["status"] == "indexed"
    assert by_uri["https://docs.example.com/pricing"]["title"] == "Pricing page"

    refreshed = await client.get(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert refreshed.json()["status"] == "idle"
    assert refreshed.json()["last_synced_at"]

    # re-sync is idempotent: same URLs upsert the same documents
    with respx.mock:
        respx.get("https://docs.example.com/pricing").mock(
            return_value=httpx.Response(
                200, content=HTML_PAGE, headers={"content-type": "text/html"}
            )
        )
        respx.get("https://docs.example.com/faq").mock(
            return_value=httpx.Response(
                200,
                content=(
                    b"<html><head><title>FAQ</title></head>"
                    b"<body><p>Answers live here.</p></body></html>"
                ),
                headers={"content-type": "text/html"},
            )
        )
        await client.post(
            f"{workspace_ctx.base}/knowledge/sources/{source['id']}/sync",
            headers=workspace_ctx.owner_headers,
        )
        await drain_tasks()
    documents = await client.get(
        f"{workspace_ctx.base}/knowledge/documents?source_id={source['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert documents.json()["total"] == 2


async def test_sync_url_failure_sets_source_error_but_indexes_rest(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="urls",
        config={"urls": ["https://site.example.com/ok", "https://site.example.com/missing"]},
    )
    with respx.mock:
        respx.get("https://site.example.com/ok").mock(
            return_value=httpx.Response(
                200, content=HTML_PAGE, headers={"content-type": "text/html"}
            )
        )
        respx.get("https://site.example.com/missing").mock(
            return_value=httpx.Response(404, content=b"nope")
        )
        await client.post(
            f"{workspace_ctx.base}/knowledge/sources/{source['id']}/sync",
            headers=workspace_ctx.owner_headers,
        )
        await drain_tasks()

    refreshed = await client.get(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert refreshed.json()["status"] == "error"
    assert "https://site.example.com/missing" in refreshed.json()["error"]
    assert "404" in refreshed.json()["error"]

    documents = await client.get(
        f"{workspace_ctx.base}/knowledge/documents?source_id={source['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert documents.json()["total"] == 1  # the good page still indexed
    assert documents.json()["items"][0]["status"] == "indexed"


async def test_sync_rejects_oversized_and_non_html(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="urls",
        config={"urls": ["https://big.example.com/page", "https://plain.example.com/file"]},
    )
    with respx.mock:
        respx.get("https://big.example.com/page").mock(
            return_value=httpx.Response(
                200,
                content=b"<html>" + b"x" * (2 * 1024 * 1024 + 100),
                headers={"content-type": "text/html"},
            )
        )
        respx.get("https://plain.example.com/file").mock(
            return_value=httpx.Response(
                200, content=b"just text", headers={"content-type": "text/plain"}
            )
        )
        await client.post(
            f"{workspace_ctx.base}/knowledge/sources/{source['id']}/sync",
            headers=workspace_ctx.owner_headers,
        )
        await drain_tasks()

    refreshed = await client.get(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert refreshed.json()["status"] == "error"
    assert "2MB" in refreshed.json()["error"]
    assert "text/plain" in refreshed.json()["error"]
    documents = await client.get(
        f"{workspace_ctx.base}/knowledge/documents?source_id={source['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert documents.json()["total"] == 0
