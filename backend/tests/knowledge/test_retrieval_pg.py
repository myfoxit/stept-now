"""Postgres-path retrieval: pgvector `<=>` + ts_rank_cd → RRF.

Run with a reachable Postgres (pgvector image), e.g.:
    STEPT_TEST_PG_URL=postgresql+asyncpg://stept:stept@localhost:54329/stept uv run pytest -m pg
"""

from __future__ import annotations

import os

import pytest

from app.rag.seed import BILLING_DOC_TITLE, WIDGET_DOC_TITLE

pytestmark = pytest.mark.pg


@pytest.fixture
async def pg_session(monkeypatch):
    """Session against the real Postgres, seeded with a throwaway workspace."""
    monkeypatch.setenv("STEPT_DATABASE_URL", os.environ["STEPT_TEST_PG_URL"])
    from app.core.config import reset_settings_cache
    from app.core.db import dispose_engine, get_session_factory, init_db, uuid7

    reset_settings_cache()
    await dispose_engine()  # drop any engine built against sqlite
    await init_db()

    from app.models.workspace import Workspace

    workspace = Workspace(name="PG Test", slug=f"pg-test-{uuid7()}")
    async with get_session_factory()() as session:
        session.add(workspace)
        await session.commit()
        workspace_id = workspace.id  # plain str — safe after rollback expiry
        try:
            yield session, workspace_id
        finally:
            await session.rollback()
            deleted = await session.get(Workspace, workspace_id)
            if deleted is not None:
                await session.delete(deleted)  # FK cascade removes sources/docs/chunks
                await session.commit()
    await dispose_engine()


async def _seed_docs(session, workspace_id: str):
    from app.models.knowledge import Document, KnowledgeSource
    from app.rag.ingestion import ingest_document
    from app.rag.seed import _DOCS

    source = KnowledgeSource(workspace_id=workspace_id, type="text", name="PG docs")
    session.add(source)
    await session.flush()
    for title, text in _DOCS:
        document = Document(
            workspace_id=workspace_id,
            source_id=source.id,
            title=title,
            mime="text/markdown",
            status="pending",
        )
        session.add(document)
        await session.flush()
        await ingest_document(session, document, text)
    await session.commit()
    return source


async def test_pg_hybrid_search_relevance_and_shape(pg_session):
    from app.core.db import is_postgres
    from app.rag.retrieval import RetrievedChunk, search_chunks

    session, workspace_id = pg_session
    assert is_postgres(session), "fixture must run against Postgres"
    await _seed_docs(session, workspace_id)

    widget = await search_chunks(session, workspace_id, "how do I install the chat widget", k=4)
    assert widget, "expected results from the SQL hybrid path"
    assert isinstance(widget[0], RetrievedChunk)
    assert widget[0].title == WIDGET_DOC_TITLE
    first = widget[0]
    assert first.chunk_id and first.document_id and first.score > 0
    assert first.ord >= 0

    refund = await search_chunks(session, workspace_id, "refund", k=4)
    assert refund[0].title == BILLING_DOC_TITLE
    # ts stemming: "refund" must match "refunds"/"refunded" via the english config
    assert "refund" in refund[0].content.lower()


async def test_pg_source_filter_and_neighbor_expansion(pg_session):
    from app.rag.retrieval import search_chunks

    session, workspace_id = pg_session
    source = await _seed_docs(session, workspace_id)

    filtered = await search_chunks(
        session, workspace_id, "install the widget", k=3, source_ids=[source.id]
    )
    assert filtered
    empty = await search_chunks(
        session,
        workspace_id,
        "install the widget",
        k=3,
        source_ids=["00000000-0000-0000-0000-000000000000"],
    )
    assert empty == []

    expanded = await search_chunks(session, workspace_id, "refund", k=1, expand_neighbors=True)
    plain = await search_chunks(session, workspace_id, "refund", k=1, expand_neighbors=False)
    assert expanded[0].chunk_id == plain[0].chunk_id
    assert len(expanded[0].content) > len(plain[0].content)
