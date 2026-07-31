"""Help-center articles: CRUD, publish→searchable→unpublish, public portal."""

from __future__ import annotations

from tests.knowledge.conftest import search

BODY = """## Exporting your data

Go to Settings → Data export and click "Request export". You will receive an
email with a download link within a few minutes. Exports include contacts,
conversations and knowledge documents as JSON, and attachments as files.
"""


async def make_article(client, ctx, *, title="Exporting your data", body=BODY, **extra):
    response = await client.post(
        f"{ctx.base}/articles",
        json={"title": title, "body": body, **extra},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_collection_crud_and_slug(client, workspace_ctx):
    created = await client.post(
        f"{workspace_ctx.base}/articles/collections",
        json={"name": "Getting Started", "icon": "🚀", "ord": 1},
        headers=workspace_ctx.owner_headers,
    )
    assert created.status_code == 201, created.text
    collection = created.json()
    assert collection["slug"] == "getting-started"

    # auto-dedupe when the generated slug collides
    twin = await client.post(
        f"{workspace_ctx.base}/articles/collections",
        json={"name": "Getting Started"},
        headers=workspace_ctx.owner_headers,
    )
    assert twin.json()["slug"] == "getting-started-2"

    # explicit duplicate slug conflicts
    conflict = await client.post(
        f"{workspace_ctx.base}/articles/collections",
        json={"name": "Other", "slug": "getting-started"},
        headers=workspace_ctx.owner_headers,
    )
    assert conflict.status_code == 409

    patched = await client.patch(
        f"{workspace_ctx.base}/articles/collections/{collection['id']}",
        json={"name": "Start Here", "description": "First steps"},
        headers=workspace_ctx.owner_headers,
    )
    assert patched.json()["name"] == "Start Here"

    listed = await client.get(
        f"{workspace_ctx.base}/articles/collections", headers=workspace_ctx.owner_headers
    )
    assert len(listed.json()) == 2

    # deleting a collection detaches its articles instead of deleting them
    article = await make_article(client, workspace_ctx, collection_id=collection["id"])
    deleted = await client.delete(
        f"{workspace_ctx.base}/articles/collections/{collection['id']}",
        headers=workspace_ctx.owner_headers,
    )
    assert deleted.status_code == 200
    detached = await client.get(
        f"{workspace_ctx.base}/articles/{article['id']}", headers=workspace_ctx.owner_headers
    )
    assert detached.json()["collection_id"] is None


async def test_article_crud_defaults_to_draft(client, workspace_ctx):
    article = await make_article(client, workspace_ctx)
    assert article["status"] == "draft"
    assert article["slug"] == "exporting-your-data"
    assert article["published_at"] is None

    patched = await client.patch(
        f"{workspace_ctx.base}/articles/{article['id']}",
        json={"title": "Export guide"},
        headers=workspace_ctx.owner_headers,
    )
    assert patched.json()["title"] == "Export guide"
    assert patched.json()["slug"] == "exporting-your-data"  # slug unchanged unless sent

    listing = await client.get(
        f"{workspace_ctx.base}/articles?status=draft", headers=workspace_ctx.owner_headers
    )
    assert listing.json()["total"] == 1
    assert "body" not in listing.json()["items"][0]

    deleted = await client.delete(
        f"{workspace_ctx.base}/articles/{article['id']}", headers=workspace_ctx.owner_headers
    )
    assert deleted.status_code == 200
    assert (
        await client.get(
            f"{workspace_ctx.base}/articles/{article['id']}", headers=workspace_ctx.owner_headers
        )
    ).status_code == 404


async def test_publish_makes_article_searchable(client, workspace_ctx):
    article = await make_article(client, workspace_ctx)
    before = await search(client, workspace_ctx, "how do I export my data")
    assert before["results"] == []  # drafts are not searchable

    published = await client.post(
        f"{workspace_ctx.base}/articles/{article['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["published_at"]

    after = await search(client, workspace_ctx, "how do I export my data")
    assert after["results"], "published article must be searchable immediately (no drain)"
    top = after["results"][0]
    assert top["title"] == "Exporting your data"
    assert top["url"] == f"/portal/{workspace_ctx.workspace['slug']}/articles/{article['slug']}"

    # the auto-created articles source is a singleton
    second = await make_article(client, workspace_ctx, title="Another guide", body="More words.")
    await client.post(
        f"{workspace_ctx.base}/articles/{second['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    sources = (
        await client.get(
            f"{workspace_ctx.base}/knowledge/sources", headers=workspace_ctx.owner_headers
        )
    ).json()
    articles_sources = [s for s in sources if s["type"] == "articles"]
    assert len(articles_sources) == 1
    assert articles_sources[0]["document_count"] == 2


async def test_unpublish_removes_from_search_and_portal(client, workspace_ctx):
    article = await make_article(client, workspace_ctx)
    await client.post(
        f"{workspace_ctx.base}/articles/{article['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    assert (await search(client, workspace_ctx, "export my data"))["results"]

    unpublished = await client.post(
        f"{workspace_ctx.base}/articles/{article['id']}/unpublish",
        headers=workspace_ctx.owner_headers,
    )
    assert unpublished.json()["status"] == "draft"
    assert (await search(client, workspace_ctx, "export my data"))["results"] == []

    slug = workspace_ctx.workspace["slug"]
    portal = await client.get(f"/portal/{slug}/articles/{article['slug']}")
    assert portal.status_code == 404


async def test_updating_published_article_resyncs_index(client, workspace_ctx):
    article = await make_article(client, workspace_ctx)
    await client.post(
        f"{workspace_ctx.base}/articles/{article['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    await client.patch(
        f"{workspace_ctx.base}/articles/{article['id']}",
        json={"body": "## New content\n\nZanzibar shipments arrive on Thursdays."},
        headers=workspace_ctx.owner_headers,
    )
    results = (await search(client, workspace_ctx, "zanzibar shipments"))["results"]
    assert results
    assert "Zanzibar" in results[0]["content"]
    stale = (await search(client, workspace_ctx, "export my data download link"))["results"]
    assert all("download link" not in r["content"] for r in stale)


async def test_deleting_published_article_removes_document(client, workspace_ctx):
    article = await make_article(client, workspace_ctx)
    await client.post(
        f"{workspace_ctx.base}/articles/{article['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    await client.delete(
        f"{workspace_ctx.base}/articles/{article['id']}", headers=workspace_ctx.owner_headers
    )
    assert (await search(client, workspace_ctx, "export my data"))["results"] == []
    documents = await client.get(
        f"{workspace_ctx.base}/knowledge/documents", headers=workspace_ctx.owner_headers
    )
    assert documents.json()["total"] == 0


async def test_portal_home_is_public_and_published_only(client, workspace_ctx):
    collection = (
        await client.post(
            f"{workspace_ctx.base}/articles/collections",
            json={"name": "Guides", "icon": "📖", "description": "How-tos"},
            headers=workspace_ctx.owner_headers,
        )
    ).json()
    published = await make_article(client, workspace_ctx, collection_id=collection["id"])
    await client.post(
        f"{workspace_ctx.base}/articles/{published['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    draft = await make_article(
        client,
        workspace_ctx,
        title="Secret draft",
        body="Not ready.",
        collection_id=collection["id"],
    )
    uncollected = await make_article(client, workspace_ctx, title="Loose article", body="Hi.")
    await client.post(
        f"{workspace_ctx.base}/articles/{uncollected['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )

    slug = workspace_ctx.workspace["slug"]
    home = await client.get(f"/portal/{slug}")  # NO auth header
    assert home.status_code == 200
    body = home.json()
    assert body["workspace"]["name"] == "Acme Support"
    guides = next(c for c in body["collections"] if c["slug"] == "guides")
    assert [a["slug"] for a in guides["articles"]] == [published["slug"]]
    assert all(a["slug"] != draft["slug"] for c in body["collections"] for a in c["articles"])
    other = next(c for c in body["collections"] if c["slug"] == "other")
    assert [a["slug"] for a in other["articles"]] == [uncollected["slug"]]

    assert (await client.get("/portal/nope-does-not-exist")).status_code == 404


async def test_portal_article_public_fetch_and_draft_404(client, workspace_ctx):
    collection = (
        await client.post(
            f"{workspace_ctx.base}/articles/collections",
            json={"name": "Guides"},
            headers=workspace_ctx.owner_headers,
        )
    ).json()
    article = await make_article(client, workspace_ctx, collection_id=collection["id"])
    slug = workspace_ctx.workspace["slug"]

    assert (await client.get(f"/portal/{slug}/articles/{article['slug']}")).status_code == 404

    await client.post(
        f"{workspace_ctx.base}/articles/{article['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    fetched = await client.get(f"/portal/{slug}/articles/{article['slug']}")  # NO auth
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["title"] == "Exporting your data"
    assert "Request export" in body["body"]
    assert body["collection"] == {"name": "Guides", "slug": "guides"}
    assert body["published_at"]

    assert (await client.get(f"/portal/{slug}/articles/unknown-slug")).status_code == 404


async def test_article_slug_conflict_and_collection_validation(client, workspace_ctx):
    await make_article(client, workspace_ctx, title="Guide", slug="guide")
    conflict = await client.post(
        f"{workspace_ctx.base}/articles",
        json={"title": "Guide 2", "slug": "guide", "body": ""},
        headers=workspace_ctx.owner_headers,
    )
    assert conflict.status_code == 409

    unknown_collection = await client.post(
        f"{workspace_ctx.base}/articles",
        json={"title": "Bad", "body": "", "collection_id": "00000000-0000-0000-0000-000000000000"},
        headers=workspace_ctx.owner_headers,
    )
    assert unknown_collection.status_code == 404
