"""Authored-document editing: PATCH /knowledge/documents/{id} + raw content."""

from __future__ import annotations

import httpx
import respx

from tests.conftest import bearer, drain_tasks, signup
from tests.knowledge.conftest import (
    create_source,
    get_doc,
    html_page,
    paste_text,
    search,
    sync_now,
)

ORIGINAL = "# Refund policy\n\nWe refund every purchase within 30 days."
UPDATED = "# Refund policy\n\nRefunds are issued within 14 days of purchase."


async def patch_doc(client, ctx, document_id, body, *, headers=None):
    return await client.patch(
        f"{ctx.base}/knowledge/documents/{document_id}",
        json=body,
        headers=headers or ctx.owner_headers,
    )


async def authored_doc(client, ctx, *, content=ORIGINAL, title="Refund policy"):
    source = await create_source(client, ctx, type="text", name="Authored")
    document = await paste_text(client, ctx, source["id"], title=title, content=content)
    await drain_tasks()
    return source, document


async def test_detail_exposes_raw_content_for_authored_documents(client, workspace_ctx):
    _, document = await authored_doc(client, workspace_ctx)
    detail = await get_doc(client, workspace_ctx, document["id"])
    assert detail["content"] == ORIGINAL
    assert detail["status"] == "indexed"
    assert detail["chunks"]


async def test_detail_content_is_none_for_connector_documents(client, workspace_ctx):
    source = await create_source(
        client,
        workspace_ctx,
        type="urls",
        name="Website",
        config={"urls": ["https://docs.example.com/pricing"]},
    )
    with respx.mock:
        respx.get("https://docs.example.com/pricing").mock(
            return_value=html_page("Pricing", "Pro costs 49 dollars.")
        )
        await sync_now(client, workspace_ctx, source["id"])

    listed = await client.get(
        f"{workspace_ctx.base}/knowledge/documents?source_id={source['id']}",
        headers=workspace_ctx.owner_headers,
    )
    document = listed.json()["items"][0]
    detail = await get_doc(client, workspace_ctx, document["id"])
    assert detail["content"] is None
    assert detail["chunks"]  # everything else about the detail payload is unchanged


async def test_patch_content_reingests_inline(client, workspace_ctx):
    _, document = await authored_doc(client, workspace_ctx)
    before = await get_doc(client, workspace_ctx, document["id"])

    response = await patch_doc(client, workspace_ctx, document["id"], {"content": UPDATED})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "indexed"  # indexed inline, no queue round-trip
    assert body["content_hash"] != before["content_hash"]
    assert body["token_count"] > 0

    detail = await get_doc(client, workspace_ctx, document["id"])
    assert detail["content"] == UPDATED
    assert "14 days" in "\n".join(chunk["content"] for chunk in detail["chunks"])
    assert detail["meta"]["size"] == len(UPDATED.encode())

    found = await search(client, workspace_ctx, "refunds issued 14 days")
    assert any("14 days" in result["content"] for result in found["results"])


async def test_patch_with_identical_content_is_a_hash_stable_noop(client, workspace_ctx):
    _, document = await authored_doc(client, workspace_ctx)
    before = await get_doc(client, workspace_ctx, document["id"])

    response = await patch_doc(client, workspace_ctx, document["id"], {"content": ORIGINAL})
    assert response.status_code == 200
    assert response.json()["content_hash"] == before["content_hash"]

    after = await get_doc(client, workspace_ctx, document["id"])
    assert [c["id"] for c in after["chunks"]] == [c["id"] for c in before["chunks"]]
    assert after["status"] == "indexed"


async def test_patch_title_rechunks_with_the_new_title(client, workspace_ctx):
    _, document = await authored_doc(client, workspace_ctx)
    response = await patch_doc(client, workspace_ctx, document["id"], {"title": "Returns policy"})
    assert response.status_code == 200
    assert response.json()["title"] == "Returns policy"

    detail = await get_doc(client, workspace_ctx, document["id"])
    assert detail["status"] == "indexed"
    assert detail["chunks"][0]["content"].startswith("# Returns policy")
    assert detail["content"] == ORIGINAL  # body untouched


async def test_patch_title_and_content_together(client, workspace_ctx):
    _, document = await authored_doc(client, workspace_ctx)
    response = await patch_doc(
        client,
        workspace_ctx,
        document["id"],
        {"title": "Returns policy", "content": UPDATED},
    )
    assert response.status_code == 200
    detail = await get_doc(client, workspace_ctx, document["id"])
    assert detail["title"] == "Returns policy"
    assert detail["content"] == UPDATED
    assert detail["meta"]["filename"] == "Returns policy.md"


async def test_patch_uploaded_text_file_is_editable(client, workspace_ctx):
    source = await create_source(client, workspace_ctx, type="files", name="Uploads")
    upload = await client.post(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}/documents",
        files={"file": ("hours.txt", b"Support hours are 9am to 6pm.", "text/plain")},
        headers=workspace_ctx.owner_headers,
    )
    assert upload.status_code == 201
    await drain_tasks()

    response = await patch_doc(
        client, workspace_ctx, upload.json()["id"], {"content": "Support hours are 8am to 8pm."}
    )
    assert response.status_code == 200
    detail = await get_doc(client, workspace_ctx, upload.json()["id"])
    assert detail["content"] == "Support hours are 8am to 8pm."
    assert detail["meta"]["filename"].endswith(".txt")  # extension preserved


async def test_patch_rejects_non_editable_documents_with_409(client, workspace_ctx):
    # 1. a URL-backed document from a connector source
    urls_source = await create_source(
        client,
        workspace_ctx,
        type="urls",
        name="Website",
        config={"urls": ["https://docs.example.com/pricing"]},
    )
    with respx.mock:
        respx.get("https://docs.example.com/pricing").mock(
            return_value=html_page("Pricing", "Pro costs 49 dollars.")
        )
        await sync_now(client, workspace_ctx, urls_source["id"])
    url_doc = (
        await client.get(
            f"{workspace_ctx.base}/knowledge/documents?source_id={urls_source['id']}",
            headers=workspace_ctx.owner_headers,
        )
    ).json()["items"][0]
    denied = await patch_doc(client, workspace_ctx, url_doc["id"], {"content": "nope"})
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "conflict"

    # 2. a portal-path document mirrored from a published article
    article = await client.post(
        f"{workspace_ctx.base}/articles",
        json={"title": "Getting started", "body": "Install the widget."},
        headers=workspace_ctx.owner_headers,
    )
    await client.post(
        f"{workspace_ctx.base}/articles/{article.json()['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    await drain_tasks()
    sources = (
        await client.get(
            f"{workspace_ctx.base}/knowledge/sources", headers=workspace_ctx.owner_headers
        )
    ).json()
    articles_source = next(s for s in sources if s["type"] == "articles")
    article_doc = (
        await client.get(
            f"{workspace_ctx.base}/knowledge/documents?source_id={articles_source['id']}",
            headers=workspace_ctx.owner_headers,
        )
    ).json()["items"][0]
    assert (await get_doc(client, workspace_ctx, article_doc["id"]))["content"] is None
    denied_article = await patch_doc(client, workspace_ctx, article_doc["id"], {"title": "Nope"})
    assert denied_article.status_code == 409


async def test_patch_rejects_non_text_uploads_with_409(client, workspace_ctx):
    source = await create_source(client, workspace_ctx, type="files", name="Uploads")
    upload = await client.post(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}/documents",
        files={"file": ("report.csv", b"name,plan\nada,pro\n", "text/csv")},
        headers=workspace_ctx.owner_headers,
    )
    assert upload.status_code == 201
    await drain_tasks()
    detail = await get_doc(client, workspace_ctx, upload.json()["id"])
    assert detail["content"] is None  # csv is extracted, not authored
    denied = await patch_doc(client, workspace_ctx, upload.json()["id"], {"content": "x"})
    assert denied.status_code == 409


async def test_patch_validation_authz_and_isolation(client, workspace_ctx):
    _, document = await authored_doc(client, workspace_ctx)

    blank_title = await patch_doc(client, workspace_ctx, document["id"], {"title": "   "})
    assert blank_title.status_code == 422
    missing = await patch_doc(client, workspace_ctx, "019f0000-0000-7000-8000-000000000000", {})
    assert missing.status_code == 404

    viewer = await workspace_ctx.add_member("viewer@example.com", role="viewer")
    denied = await patch_doc(
        client, workspace_ctx, document["id"], {"content": UPDATED}, headers=viewer
    )
    assert denied.status_code == 403

    other_auth = await signup(client, "spy@example.com")
    other_ws = (
        await client.post("/api/v1/workspaces", json={"name": "Spy Co"}, headers=bearer(other_auth))
    ).json()
    cross: httpx.Response = await client.patch(
        f"/api/v1/w/{other_ws['id']}/knowledge/documents/{document['id']}",
        json={"content": UPDATED},
        headers=bearer(other_auth),
    )
    assert cross.status_code == 404
    assert (await get_doc(client, workspace_ctx, document["id"]))["content"] == ORIGINAL
