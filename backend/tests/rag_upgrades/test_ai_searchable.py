"""Document.ai_searchable: default-on retrieval opt-out (old-repo `rag_indexed`
parity). Excluded from search_chunks on the query AND neighbor-expansion reads;
toggled via PATCH on any document; widget article search keeps working."""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.knowledge import Document
from app.rag.retrieval import search_chunks
from tests.conftest import drain_tasks
from tests.knowledge.conftest import create_source, get_doc, paste_text
from tests.rag_upgrades.conftest import build_corpus


async def patch_doc(client, ctx, document_id, body, *, headers=None):
    return await client.patch(
        f"{ctx.base}/knowledge/documents/{document_id}",
        json=body,
        headers=headers or ctx.owner_headers,
    )


async def set_flag(workspace_id: str, document_id: str, value: bool) -> None:
    async with get_session_factory()() as session:
        document = await session.get(Document, document_id)
        assert document is not None and document.workspace_id == workspace_id
        document.ai_searchable = value
        await session.commit()


# --- default + PATCH ---------------------------------------------------------


async def test_documents_default_to_ai_searchable(client, workspace_ctx, session):
    source = await create_source(client, workspace_ctx, type="text", name="Docs")
    document = await paste_text(
        client, workspace_ctx, source["id"], title="Refunds", content="Refunds within 30 days."
    )
    await drain_tasks()
    assert document["ai_searchable"] is True
    detail = await get_doc(client, workspace_ctx, document["id"])
    assert detail["ai_searchable"] is True

    row = (
        await session.execute(select(Document).where(Document.id == document["id"]))
    ).scalar_one()
    assert row.ai_searchable is True


async def test_patch_toggles_the_flag_without_reindexing(client, workspace_ctx):
    source = await create_source(client, workspace_ctx, type="text", name="Docs")
    document = await paste_text(
        client, workspace_ctx, source["id"], title="Refunds", content="Refunds within 30 days."
    )
    await drain_tasks()
    before = await get_doc(client, workspace_ctx, document["id"])

    off = await patch_doc(client, workspace_ctx, document["id"], {"ai_searchable": False})
    assert off.status_code == 200, off.text
    assert off.json()["ai_searchable"] is False

    after = await get_doc(client, workspace_ctx, document["id"])
    assert after["ai_searchable"] is False
    assert after["status"] == "indexed", "flag-only PATCH must not reprocess"
    assert [c["id"] for c in after["chunks"]] == [c["id"] for c in before["chunks"]]

    on = await patch_doc(client, workspace_ctx, document["id"], {"ai_searchable": True})
    assert on.status_code == 200
    assert on.json()["ai_searchable"] is True


async def test_flag_toggles_on_non_editable_documents_but_text_edits_still_409(
    client, workspace_ctx
):
    # An article-mirror document: portal-backed, so title/content edits are 409.
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

    toggled = await patch_doc(client, workspace_ctx, article_doc["id"], {"ai_searchable": False})
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["ai_searchable"] is False

    still_denied = await patch_doc(
        client, workspace_ctx, article_doc["id"], {"title": "Nope", "ai_searchable": True}
    )
    assert still_denied.status_code == 409
    # The rejected request must not have half-applied the flag.
    assert (await get_doc(client, workspace_ctx, article_doc["id"]))["ai_searchable"] is False


async def test_patch_flag_requires_knowledge_write(client, workspace_ctx):
    source = await create_source(client, workspace_ctx, type="text", name="Docs")
    document = await paste_text(
        client, workspace_ctx, source["id"], title="Refunds", content="Refunds within 30 days."
    )
    await drain_tasks()
    viewer = await workspace_ctx.add_member("viewer-flag@example.com", role="viewer")
    denied = await patch_doc(
        client, workspace_ctx, document["id"], {"ai_searchable": False}, headers=viewer
    )
    assert denied.status_code == 403


# --- retrieval exclusion -----------------------------------------------------


async def test_flag_off_excludes_chunks_from_retrieval(session, ws):
    _, ids = await build_corpus(
        session,
        ws,
        [
            ("Billing overview", ["The billing plan renews monthly."]),
            ("Billing secrets", ["The billing plan discount code is HIDDEN42."]),
        ],
    )

    before = await search_chunks(session, ws, "billing plan", k=8, history=[])
    assert {r.document_id for r in before} == set(ids.values())

    secrets = await session.get(Document, ids["Billing secrets"])
    assert secrets is not None
    secrets.ai_searchable = False
    await session.flush()

    after = await search_chunks(session, ws, "billing plan", k=8, history=[])
    assert after, "the searchable document must still be found"
    assert {r.document_id for r in after} == {ids["Billing overview"]}
    assert all("HIDDEN42" not in r.content for r in after)


async def test_excluded_document_never_leaks_via_neighbor_expansion(session, ws):
    _, ids = await build_corpus(
        session,
        ws,
        [
            (
                "Public guide",
                [
                    "Intro neighbor text about setup.",
                    "The billing plan renews monthly.",
                    "Outro neighbor text about support.",
                ],
            ),
            (
                "Private notes",
                [
                    "SECRET-B0 internal only.",
                    "The billing plan margin is SECRET-B1.",
                    "SECRET-B2 internal only.",
                ],
            ),
        ],
    )
    private = await session.get(Document, ids["Private notes"])
    assert private is not None
    private.ai_searchable = False
    await session.flush()

    results = await search_chunks(session, ws, "billing plan", k=8, history=[])
    assert results
    assert {r.document_id for r in results} == {ids["Public guide"]}
    joined = "\n".join(r.content for r in results)
    assert "SECRET-B" not in joined, "excluded doc leaked through ±1 neighbor expansion"
    # Sanity: expansion itself worked — the public hit grew its neighbors.
    assert "Intro neighbor text" in joined or "Outro neighbor text" in joined


# --- widget article search ---------------------------------------------------


async def test_widget_article_search_unaffected_for_published_articles(client, workspace_ctx):
    from app.api.widget.articles import _search_articles

    article = await client.post(
        f"{workspace_ctx.base}/articles",
        json={"title": "Refund policy", "body": "We refund every purchase within 30 days."},
        headers=workspace_ctx.owner_headers,
    )
    await client.post(
        f"{workspace_ctx.base}/articles/{article.json()['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    await drain_tasks()

    # An unrelated knowledge document opts out — article search must not care.
    source = await create_source(client, workspace_ctx, type="text", name="Docs")
    other = await paste_text(
        client, workspace_ctx, source["id"], title="Refund internals", content="Refund margins."
    )
    await drain_tasks()
    await set_flag(workspace_ctx.id, other["id"], False)

    async with get_session_factory()() as session:
        results = await _search_articles(session, workspace_ctx.id, "refund")
    assert [r.slug for r in results] == ["refund-policy"]

    # Opting the article's own mirror document out removes it from widget search
    # too — the flag means "not AI searchable", regardless of the source.
    async with get_session_factory()() as session:
        mirror = (
            await session.execute(
                select(Document).where(
                    Document.workspace_id == workspace_ctx.id,
                    Document.title == "Refund policy",
                )
            )
        ).scalar_one()
        mirror_id = mirror.id
    await set_flag(workspace_ctx.id, mirror_id, False)
    async with get_session_factory()() as session:
        assert await _search_articles(session, workspace_ctx.id, "refund") == []
