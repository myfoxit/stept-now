"""Global GET /search document leg: PG-only as-you-type upgrades (prefix FTS,
trgm typo fallback, short-query title ILIKE) with the SQLite path untouched."""

from __future__ import annotations

import pytest

from app.api.v1 import search as search_module
from app.api.v1.search import SearchDocument, prefix_tsquery
from tests.rag_upgrades.conftest import build_corpus

PG_DOCS = [
    ("Widget guide", ["Installing the chat widget takes five minutes."]),
    ("Billing settings", ["Billing settings."]),
    ("FAQ", ["Frequently asked questions about everything."]),
]


# --- pure unit ---------------------------------------------------------------


def test_prefix_tsquery_marks_only_the_last_token():
    assert prefix_tsquery("install the wid") == "install & the & wid:*"
    assert prefix_tsquery("instal") == "instal:*"
    assert prefix_tsquery("a&b|c!") == "a & b & c:*"  # operators can't reach tsquery
    assert prefix_tsquery("?!") is None
    assert prefix_tsquery("") is None


# --- SQLite: the path is untouched ------------------------------------------


async def test_sqlite_documents_leg_still_uses_hybrid_search(session, ws, monkeypatch):
    _, ids = await build_corpus(
        session, ws, [("Billing overview", ["The billing plan renews monthly."])]
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("the PG document leg must never run on SQLite")

    calls: list[dict] = []
    real_search_chunks = search_module.search_chunks

    async def spying_search_chunks(session_, workspace_id, q, **kwargs):
        calls.append({"q": q, **kwargs})
        return await real_search_chunks(session_, workspace_id, q, **kwargs)

    monkeypatch.setattr(search_module, "_documents_pg", forbidden)
    monkeypatch.setattr(search_module, "search_chunks", spying_search_chunks)

    out = await search_module._documents(session, ws, "billing plan", 5)
    assert [d.document_id for d in out] == [ids["Billing overview"]]
    assert calls == [{"q": "billing plan", "k": 5, "expand_neighbors": False}]

    # Short queries take no special branch on SQLite either.
    await search_module._documents(session, ws, "bi", 5)
    assert calls[-1] == {"q": "bi", "k": 5, "expand_neighbors": False}


# --- Postgres: prefix FTS → trgm fallback → title ILIKE ----------------------

pg = pytest.mark.pg


@pg
async def test_pg_prefix_tsquery_matches_a_partial_last_token(pg_session):
    session, workspace_id = pg_session
    _, ids = await build_corpus(session, workspace_id, PG_DOCS, embed=False)
    await session.commit()

    for query in ("instal", "chat widg"):
        out = await search_module._documents(session, workspace_id, query, 5)
        assert [d.document_id for d in out] == [ids["Widget guide"]], f"query {query!r}"
        assert out[0].title == "Widget guide"
        assert "chat widget" in out[0].snippet.lower()
        assert out[0].score > 0


@pg
async def test_pg_trgm_fallback_catches_typos_fts_misses(pg_session):
    session, workspace_id = pg_session
    _, ids = await build_corpus(session, workspace_id, PG_DOCS, embed=False)
    await session.commit()

    out = await search_module._documents(session, workspace_id, "biling setings", 5)
    assert [d.document_id for d in out] == [ids["Billing settings"]]
    assert out[0].score > search_module.TRGM_SIMILARITY_THRESHOLD

    # Pure noise clears neither FTS nor the similarity floor.
    assert await search_module._documents(session, workspace_id, "zzqqxxy", 5) == []


@pg
async def test_pg_short_queries_use_title_ilike(pg_session):
    session, workspace_id = pg_session
    _, ids = await build_corpus(session, workspace_id, PG_DOCS, embed=False)
    await session.commit()

    out = await search_module._documents(session, workspace_id, "fa", 5)
    assert [d.document_id for d in out] == [ids["FAQ"]]
    assert out[0].snippet.startswith("Frequently asked")
    assert await search_module._documents(session, workspace_id, "xq", 5) == []


@pg
async def test_pg_response_shape_is_identical_to_sqlite(pg_session):
    session, workspace_id = pg_session
    await build_corpus(session, workspace_id, PG_DOCS, embed=False)
    await session.commit()

    for query in ("instal", "biling setings", "fa"):
        for item in await search_module._documents(session, workspace_id, query, 5):
            assert isinstance(item, SearchDocument)
            assert set(item.model_dump()) == {"document_id", "title", "snippet", "score"}


@pg
async def test_pg_document_leg_respects_ai_searchable(pg_session):
    from app.models.knowledge import Document

    session, workspace_id = pg_session
    _, ids = await build_corpus(session, workspace_id, PG_DOCS, embed=False)
    for document_id in ids.values():
        document = await session.get(Document, document_id)
        document.ai_searchable = False
    await session.commit()

    assert await search_module._documents(session, workspace_id, "instal", 5) == []  # FTS
    assert await search_module._documents(session, workspace_id, "biling setings", 5) == []  # trgm
    assert await search_module._documents(session, workspace_id, "fa", 5) == []  # ILIKE


@pg
async def test_pg_search_chunks_respects_ai_searchable(pg_session):
    """The retrieval exclusion holds on the raw-SQL Postgres path too."""
    from app.models.knowledge import Document
    from app.rag.retrieval import search_chunks

    session, workspace_id = pg_session
    _, ids = await build_corpus(
        session,
        workspace_id,
        [("Billing overview", ["The billing plan renews monthly."])],
        embed=False,
    )
    await session.commit()

    found = await search_chunks(session, workspace_id, "billing plan", k=5, history=[])
    assert [r.document_id for r in found] == [ids["Billing overview"]]

    document = await session.get(Document, ids["Billing overview"])
    document.ai_searchable = False
    await session.commit()

    assert await search_chunks(session, workspace_id, "billing plan", k=5, history=[]) == []
