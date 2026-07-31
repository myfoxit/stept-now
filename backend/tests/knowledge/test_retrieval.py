"""Hybrid retrieval relevance on the seeded product docs (SQLite path)."""

from __future__ import annotations

from tests.conftest import drain_tasks
from tests.knowledge.conftest import create_source, paste_text, search

WIDGET_TITLE = "Getting started & installing the chat widget"
AGENTS_TITLE = "How AI agents, approvals and handoff work"
BILLING_TITLE = "Plans, billing & refund policy (demo)"


async def test_widget_install_query_hits_widget_doc(client, seeded_ctx):
    body = await search(client, seeded_ctx, "how do I install the chat widget on my site")
    assert body["results"], "expected results on seeded docs"
    assert body["results"][0]["title"] == WIDGET_TITLE
    assert "widget" in body["results"][0]["content"].lower()


async def test_refund_query_hits_billing_doc(client, seeded_ctx):
    body = await search(client, seeded_ctx, "refund")
    assert body["results"][0]["title"] == BILLING_TITLE
    assert "refund" in body["results"][0]["content"].lower()


async def test_approvals_query_hits_agents_doc(client, seeded_ctx):
    body = await search(client, seeded_ctx, "do AI agents need approval before issuing refunds?")
    titles = [result["title"] for result in body["results"][:3]]
    assert AGENTS_TITLE in titles


async def test_k_limits_results_and_scores_sorted(client, seeded_ctx):
    body = await search(client, seeded_ctx, "widget", k=2)
    assert len(body["results"]) == 2
    scores = [result["score"] for result in body["results"]]
    assert scores == sorted(scores, reverse=True)
    assert body["latency_ms"] >= 0


async def test_source_ids_filter(client, seeded_ctx):
    sources = (
        await client.get(f"{seeded_ctx.base}/knowledge/sources", headers=seeded_ctx.owner_headers)
    ).json()
    articles_source = next(s for s in sources if s["type"] == "articles")
    docs_source = next(s for s in sources if s["name"] == "Stept product docs")

    only_articles = await search(
        client, seeded_ctx, "install the widget", source_ids=[articles_source["id"]]
    )
    assert only_articles["results"]
    assert all(
        result["url"] and result["url"].startswith("/portal/")
        for result in only_articles["results"]
    )

    only_docs = await search(
        client, seeded_ctx, "install the widget", source_ids=[docs_source["id"]]
    )
    assert only_docs["results"]
    assert all(result["url"] is None for result in only_docs["results"])


async def test_neighbor_expansion_appends_adjacent_chunks(seeded_ctx):
    from app.core.db import get_session_factory
    from app.rag.retrieval import search_chunks

    async with get_session_factory()() as session:
        expanded = await search_chunks(
            session, seeded_ctx.id, "refund policy", k=1, expand_neighbors=True
        )
        plain = await search_chunks(
            session, seeded_ctx.id, "refund policy", k=1, expand_neighbors=False
        )
    assert expanded[0].chunk_id == plain[0].chunk_id
    assert len(expanded[0].content) > len(plain[0].content)
    # the plain chunk text is contained inside the expanded content
    stripped = plain[0].content.removeprefix(f"# {plain[0].title}\n\n")
    assert stripped in expanded[0].content


async def test_neighbor_expansion_dedupes_adjacent_hits(seeded_ctx):
    from sqlalchemy import select

    from app.core.db import get_session_factory
    from app.models.knowledge import Chunk
    from app.rag.retrieval import search_chunks

    async with get_session_factory()() as session:
        results = await search_chunks(session, seeded_ctx.id, "install the widget", k=6)
        originals = {
            chunk_id: content
            for chunk_id, content in (
                await session.execute(
                    select(Chunk.id, Chunk.content).where(
                        Chunk.id.in_([r.chunk_id for r in results])
                    )
                )
            ).all()
        }
    by_document: dict[str, list] = {}
    for result in results:
        by_document.setdefault(result.document_id, []).append(result)
    multi = [group for group in by_document.values() if len(group) >= 2]
    assert multi, "expected at least one document with multiple hits"
    # within a document, another hit's core chunk text must never be duplicated
    # into a sibling result via neighbor expansion
    for group in multi:
        for target in group:
            for other in group:
                if other is target:
                    continue
                core = originals[other.chunk_id].removeprefix(f"# {other.title}\n\n")
                assert core[:200] not in target.content


async def test_workspace_isolation(client, seeded_ctx):
    from tests.conftest import bearer, signup

    other_auth = await signup(client, "rival@example.com")
    other_ws = (
        await client.post(
            "/api/v1/workspaces", json={"name": "Rival Co"}, headers=bearer(other_auth)
        )
    ).json()
    body = (
        await client.post(
            f"/api/v1/w/{other_ws['id']}/knowledge/search",
            json={"query": "refund"},
            headers=bearer(other_auth),
        )
    ).json()
    assert body["results"] == []  # seeded chunks belong to the first workspace only


async def test_empty_workspace_returns_no_results(client, workspace_ctx):
    body = await search(client, workspace_ctx, "anything at all")
    assert body["results"] == []


async def test_source_boost_hook_reorders_ties(client, workspace_ctx):
    content = "Zebra kangaroo platypus wombat. The migration corridor opens in spring."
    plain = await create_source(client, workspace_ctx, name="Plain")
    boosted = await create_source(client, workspace_ctx, name="Boosted", config={"boost": 2.0})
    await paste_text(client, workspace_ctx, plain["id"], title="Animals A", content=content)
    await paste_text(client, workspace_ctx, boosted["id"], title="Animals B", content=content)
    await drain_tasks()

    body = await search(client, workspace_ctx, "kangaroo migration corridor", k=2)
    assert len(body["results"]) == 2
    assert body["results"][0]["title"] == "Animals B"  # boosted source wins the tie
    assert body["results"][0]["score"] > body["results"][1]["score"]
