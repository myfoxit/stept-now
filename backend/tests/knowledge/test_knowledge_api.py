"""Knowledge API: source CRUD/validation, authz matrix, isolation, documents."""

from __future__ import annotations

from tests.conftest import bearer, drain_tasks, signup
from tests.knowledge.conftest import create_source, paste_text, search


async def test_source_crud_and_config_validation(client, workspace_ctx):
    source = await create_source(client, workspace_ctx, type="text", name="Notes")
    assert source["status"] == "idle"
    assert source["document_count"] == 0

    patched = await client.patch(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}",
        json={"name": "Internal notes"},
        headers=workspace_ctx.owner_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Internal notes"

    # urls sources demand a valid config
    missing = await client.post(
        f"{workspace_ctx.base}/knowledge/sources",
        json={"type": "urls", "name": "Site", "config": {}},
        headers=workspace_ctx.owner_headers,
    )
    assert missing.status_code == 422
    bad_scheme = await client.post(
        f"{workspace_ctx.base}/knowledge/sources",
        json={"type": "urls", "name": "Site", "config": {"urls": ["ftp://x"]}},
        headers=workspace_ctx.owner_headers,
    )
    assert bad_scheme.status_code == 422

    # unknown type rejected by the schema; articles type is not creatable
    unknown = await client.post(
        f"{workspace_ctx.base}/knowledge/sources",
        json={"type": "sharepoint", "name": "X"},
        headers=workspace_ctx.owner_headers,
    )
    assert unknown.status_code == 422
    articles_type = await client.post(
        f"{workspace_ctx.base}/knowledge/sources",
        json={"type": "articles", "name": "X"},
        headers=workspace_ctx.owner_headers,
    )
    assert articles_type.status_code == 422


async def test_cannot_upload_into_articles_source(client, workspace_ctx):
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
    response = await client.post(
        f"{workspace_ctx.base}/knowledge/sources/{articles_source['id']}/documents",
        json={"title": "Sneaky", "content": "nope"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 400


async def test_viewer_can_read_and_search_but_not_write(client, workspace_ctx):
    viewer = await workspace_ctx.add_member("viewer@example.com", role="viewer")
    source = await create_source(client, workspace_ctx)
    await paste_text(client, workspace_ctx, source["id"], title="Facts", content="The sky is blue.")
    await drain_tasks()

    listed = await client.get(f"{workspace_ctx.base}/knowledge/sources", headers=viewer)
    assert listed.status_code == 200
    found = await search(client, workspace_ctx, "sky", headers=viewer)
    assert found["results"]

    denied_create = await client.post(
        f"{workspace_ctx.base}/knowledge/sources",
        json={"type": "text", "name": "Nope"},
        headers=viewer,
    )
    assert denied_create.status_code == 403
    denied_upload = await client.post(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}/documents",
        json={"title": "Nope", "content": "x"},
        headers=viewer,
    )
    assert denied_upload.status_code == 403
    denied_delete = await client.delete(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}", headers=viewer
    )
    assert denied_delete.status_code == 403
    denied_publish_route = await client.post(
        f"{workspace_ctx.base}/articles",
        json={"title": "No", "body": ""},
        headers=viewer,
    )
    assert denied_publish_route.status_code == 403


async def test_agent_role_cannot_write_knowledge(client, workspace_ctx):
    agent = await workspace_ctx.add_member("agent@example.com", role="agent")
    denied = await client.post(
        f"{workspace_ctx.base}/knowledge/sources",
        json={"type": "text", "name": "Nope"},
        headers=agent,
    )
    assert denied.status_code == 403
    allowed_read = await client.get(f"{workspace_ctx.base}/knowledge/sources", headers=agent)
    assert allowed_read.status_code == 200


async def test_api_key_scopes(client, workspace_ctx):
    write_key = (
        await client.post(
            f"{workspace_ctx.base}/api-keys",
            json={"name": "writer", "scopes": ["write"]},
            headers=workspace_ctx.owner_headers,
        )
    ).json()["key"]
    read_key = (
        await client.post(
            f"{workspace_ctx.base}/api-keys",
            json={"name": "reader", "scopes": ["read"]},
            headers=workspace_ctx.owner_headers,
        )
    ).json()["key"]

    created = await client.post(
        f"{workspace_ctx.base}/knowledge/sources",
        json={"type": "text", "name": "From API"},
        headers={"Authorization": f"Bearer {write_key}"},
    )
    assert created.status_code == 201

    denied = await client.post(
        f"{workspace_ctx.base}/knowledge/sources",
        json={"type": "text", "name": "Denied"},
        headers={"Authorization": f"Bearer {read_key}"},
    )
    assert denied.status_code == 403
    searchable = await client.post(
        f"{workspace_ctx.base}/knowledge/search",
        json={"query": "anything"},
        headers={"Authorization": f"Bearer {read_key}"},
    )
    assert searchable.status_code == 200


async def test_cross_workspace_isolation(client, workspace_ctx):
    source = await create_source(client, workspace_ctx)
    document = await paste_text(
        client, workspace_ctx, source["id"], title="Private", content="Secret sauce recipe."
    )
    await drain_tasks()

    other_auth = await signup(client, "spy@example.com")
    other_ws = (
        await client.post("/api/v1/workspaces", json={"name": "Spy Co"}, headers=bearer(other_auth))
    ).json()
    other_base = f"/api/v1/w/{other_ws['id']}"

    assert (
        await client.get(
            f"{other_base}/knowledge/sources/{source['id']}", headers=bearer(other_auth)
        )
    ).status_code == 404
    assert (
        await client.get(
            f"{other_base}/knowledge/documents/{document['id']}", headers=bearer(other_auth)
        )
    ).status_code == 404
    # and the first workspace is invisible to the other's member entirely
    assert (
        await client.get(f"{workspace_ctx.base}/knowledge/sources", headers=bearer(other_auth))
    ).status_code == 403


async def test_documents_list_filters_and_pagination(client, workspace_ctx):
    source_a = await create_source(client, workspace_ctx, name="A")
    source_b = await create_source(client, workspace_ctx, name="B")
    for index in range(3):
        await paste_text(
            client, workspace_ctx, source_a["id"], title=f"A{index}", content=f"alpha {index}"
        )
    await paste_text(client, workspace_ctx, source_b["id"], title="B0", content="beta zero")
    await drain_tasks()

    all_docs = await client.get(
        f"{workspace_ctx.base}/knowledge/documents", headers=workspace_ctx.owner_headers
    )
    assert all_docs.json()["total"] == 4

    filtered = await client.get(
        f"{workspace_ctx.base}/knowledge/documents?source_id={source_a['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert filtered.json()["total"] == 3

    paged = await client.get(
        f"{workspace_ctx.base}/knowledge/documents?source_id={source_a['id']}&limit=2&offset=2",
        headers=workspace_ctx.owner_headers,
    )
    body = paged.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1
    assert body["limit"] == 2
    assert body["offset"] == 2

    indexed_only = await client.get(
        f"{workspace_ctx.base}/knowledge/documents?status=indexed",
        headers=workspace_ctx.owner_headers,
    )
    assert indexed_only.json()["total"] == 4
    failed_only = await client.get(
        f"{workspace_ctx.base}/knowledge/documents?status=failed",
        headers=workspace_ctx.owner_headers,
    )
    assert failed_only.json()["total"] == 0


async def test_delete_source_cascades_documents_and_chunks(client, workspace_ctx):
    from sqlalchemy import func, select

    from app.core.db import get_session_factory
    from app.models.knowledge import Chunk

    source = await create_source(client, workspace_ctx)
    await paste_text(
        client, workspace_ctx, source["id"], title="Gone soon", content="Ephemeral words."
    )
    await drain_tasks()

    deleted = await client.delete(
        f"{workspace_ctx.base}/knowledge/sources/{source['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert deleted.status_code == 200

    documents = await client.get(
        f"{workspace_ctx.base}/knowledge/documents", headers=workspace_ctx.owner_headers
    )
    assert documents.json()["total"] == 0
    async with get_session_factory()() as session:
        chunk_count = (
            await session.execute(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.workspace_id == workspace_ctx.id)
            )
        ).scalar_one()
    assert chunk_count == 0
    assert (await search(client, workspace_ctx, "ephemeral"))["results"] == []


async def test_search_requires_query_and_respects_bounds(client, workspace_ctx):
    empty = await client.post(
        f"{workspace_ctx.base}/knowledge/search",
        json={"query": ""},
        headers=workspace_ctx.owner_headers,
    )
    assert empty.status_code == 422
    huge_k = await client.post(
        f"{workspace_ctx.base}/knowledge/search",
        json={"query": "x", "k": 500},
        headers=workspace_ctx.owner_headers,
    )
    assert huge_k.status_code == 422
    ok = await client.post(
        f"{workspace_ctx.base}/knowledge/search",
        json={"query": "x"},
        headers=workspace_ctx.owner_headers,
    )
    assert ok.status_code == 200
    assert set(ok.json()) == {"results", "latency_ms"}
