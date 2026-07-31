"""Hybrid retrieval: dense + lexical → RRF fusion → boosts → neighbor expansion.

Mirrors Onyx's ranking distilled to Postgres/SQLite (docs/research/onyx.md):
- dense top-50 (pgvector `<=>` on PG, python cosine on SQLite)
- lexical top-50 (`ts_rank_cd`/`plainto_tsquery` on PG, stopword-stripped
  token overlap on SQLite)
- reciprocal rank fusion with k=60 and equal weights
- multiplicative hooks after fusion: recency `max(1/(1+0.5*age_years), 0.75)`
  and per-source boost (source.config["boost"], clamped to [0.5, 2.0])
- ±1 neighbor-chunk expansion at read time (chunks are stored overlap-free)

Both dialects return the exact same `RetrievedChunk` shape.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, bindparam, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import embed_texts
from app.core.db import is_postgres, utcnow
from app.models.knowledge import Chunk, Document, KnowledgeSource

RRF_K = 60
RETRIEVAL_DEPTH = 50  # per leg, before fusion
RECENCY_DECAY_PER_YEAR = 0.5
RECENCY_FLOOR = 0.75
SOURCE_BOOST_MIN, SOURCE_BOOST_MAX = 0.5, 2.0

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be but by can did do does for from had has have how i if in into is it "
    "its me my of on or our so that the their them then there these they this to us was we were "
    "what when where which who why will with you your".split()
)


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    content: str
    score: float
    title: str
    url: str | None
    ord: int


async def search_chunks(
    session: AsyncSession,
    workspace_id: str,
    query: str,
    *,
    k: int = 8,
    source_ids: list[str] | None = None,
    expand_neighbors: bool = True,
) -> list[RetrievedChunk]:
    """Workspace-scoped hybrid search returning the top-k fused chunks."""
    query = query.strip()
    if not query or k <= 0:
        return []
    query_vector = (await embed_texts(session, workspace_id, [query]))[0]

    if is_postgres(session):
        dense_ids, sparse_ids = await _ranked_ids_pg(
            session, workspace_id, query, query_vector, source_ids
        )
    else:
        dense_ids, sparse_ids = await _ranked_ids_python(
            session, workspace_id, query, query_vector, source_ids
        )

    fused = _rrf_fuse([dense_ids, sparse_ids])
    if not fused:
        return []

    rows = await _load_candidates(session, workspace_id, list(fused))
    source_boosts = await _source_boosts(session, workspace_id)
    now = utcnow()
    scored = [
        (
            fused[chunk.id]
            * _recency_boost(document_updated_at, now)
            * source_boosts.get(document_source_id, 1.0),
            chunk,
            document_title,
        )
        for chunk, document_title, document_updated_at, document_source_id in rows
    ]
    scored.sort(key=lambda item: (-item[0], item[1].document_id, item[1].ord))
    top = scored[:k]

    results = [
        RetrievedChunk(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            content=chunk.content,
            score=round(score, 6),
            title=str(chunk.meta.get("title") or document_title),
            url=chunk.meta.get("url"),
            ord=chunk.ord,
        )
        for score, chunk, document_title in top
    ]
    if expand_neighbors and results:
        await _expand_neighbors(session, workspace_id, results)
    return results


# ---------------------------------------------------------------------------
# ranked candidate legs
# ---------------------------------------------------------------------------


def _rrf_fuse(ranked_id_lists: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal rank fusion with equal weights: score = Σ 1 / (k + rank)."""
    scores: dict[str, float] = {}
    for id_list in ranked_id_lists:
        for index, chunk_id in enumerate(id_list):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + index + 1)
    return scores


def _keywords(text_value: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text_value.lower()) if w not in _STOPWORDS]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _ranked_ids_python(
    session: AsyncSession,
    workspace_id: str,
    query: str,
    query_vector: list[float],
    source_ids: list[str] | None,
) -> tuple[list[str], list[str]]:
    """SQLite path: cosine + stopword-stripped token overlap, in python."""
    stmt = select(Chunk.id, Chunk.embedding, Chunk.content).where(
        Chunk.workspace_id == workspace_id
    )
    if source_ids:
        stmt = stmt.join(Document, Document.id == Chunk.document_id).where(
            Document.source_id.in_(source_ids)
        )
    rows = (await session.execute(stmt)).all()

    dense = sorted(
        (
            (_cosine(query_vector, embedding), chunk_id)
            for chunk_id, embedding, _ in rows
            if embedding
        ),
        key=lambda item: (-item[0], item[1]),
    )
    dense_ids = [chunk_id for _, chunk_id in dense[:RETRIEVAL_DEPTH]]

    query_terms = set(_keywords(query))
    sparse_ids: list[str] = []
    if query_terms:
        sparse_scored: list[tuple[int, int, str]] = []
        for chunk_id, _, content in rows:
            term_counts = Counter(_keywords(content))
            matched = sum(1 for term in query_terms if term_counts[term] > 0)
            if matched:
                frequency = sum(term_counts[term] for term in query_terms)
                sparse_scored.append((matched, frequency, chunk_id))
        sparse_scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        sparse_ids = [chunk_id for _, _, chunk_id in sparse_scored[:RETRIEVAL_DEPTH]]
    return dense_ids, sparse_ids


async def _ranked_ids_pg(
    session: AsyncSession,
    workspace_id: str,
    query: str,
    query_vector: list[float],
    source_ids: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Postgres path: pgvector cosine distance + GIN-indexed full-text rank."""
    vector_literal = "[" + ",".join(f"{value:.8f}" for value in query_vector) + "]"
    join_sql = filter_sql = ""
    params: dict[str, object] = {"ws": workspace_id, "depth": RETRIEVAL_DEPTH}
    if source_ids:
        join_sql = "JOIN documents d ON d.id = c.document_id"
        filter_sql = "AND d.source_id IN :source_ids"
        params["source_ids"] = source_ids

    dense_stmt = text(
        f"""
        SELECT c.id FROM chunks c {join_sql}
        WHERE c.workspace_id = :ws AND c.embedding IS NOT NULL {filter_sql}
        ORDER BY c.embedding <=> CAST(:qvec AS vector), c.id
        LIMIT :depth
        """
    )
    sparse_stmt = text(
        f"""
        SELECT c.id FROM chunks c {join_sql}
        WHERE c.workspace_id = :ws {filter_sql}
          AND to_tsvector('english', c.content) @@ plainto_tsquery('english', :q)
        ORDER BY ts_rank_cd(to_tsvector('english', c.content),
                            plainto_tsquery('english', :q)) DESC, c.id
        LIMIT :depth
        """
    )
    if source_ids:
        dense_stmt = dense_stmt.bindparams(bindparam("source_ids", expanding=True))
        sparse_stmt = sparse_stmt.bindparams(bindparam("source_ids", expanding=True))

    # text() bypasses the GUID TypeDecorator: asyncpg yields uuid.UUID — normalize
    # to the string ids the ORM path uses everywhere else.
    dense_ids = [
        str(value)
        for value in (
            await session.execute(dense_stmt, {**params, "qvec": vector_literal})
        ).scalars()
    ]
    sparse_ids = [
        str(value)
        for value in (await session.execute(sparse_stmt, {**params, "q": query})).scalars()
    ]
    return dense_ids, sparse_ids


# ---------------------------------------------------------------------------
# hydration, boosts, neighbor expansion
# ---------------------------------------------------------------------------


async def _load_candidates(
    session: AsyncSession, workspace_id: str, chunk_ids: list[str]
) -> list[tuple[Chunk, str, datetime, str]]:
    rows = await session.execute(
        select(Chunk, Document.title, Document.updated_at, Document.source_id)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.workspace_id == workspace_id, Chunk.id.in_(chunk_ids))
    )
    return [tuple(row) for row in rows.all()]  # type: ignore[misc]


def _recency_boost(updated_at: datetime | None, now: datetime) -> float:
    if updated_at is None:
        return 1.0
    age_years = max((now - updated_at).total_seconds(), 0.0) / (365.25 * 24 * 3600)
    return max(1.0 / (1.0 + RECENCY_DECAY_PER_YEAR * age_years), RECENCY_FLOOR)


async def _source_boosts(session: AsyncSession, workspace_id: str) -> dict[str, float]:
    rows = await session.execute(
        select(KnowledgeSource.id, KnowledgeSource.config).where(
            KnowledgeSource.workspace_id == workspace_id
        )
    )
    boosts: dict[str, float] = {}
    for source_id, config in rows.all():
        raw = (config or {}).get("boost")
        if isinstance(raw, (int, float)):
            boosts[source_id] = min(max(float(raw), SOURCE_BOOST_MIN), SOURCE_BOOST_MAX)
    return boosts


def _strip_title_prefix(content: str, title: str) -> str:
    prefix = f"# {title}\n\n"
    return content[len(prefix) :] if content.startswith(prefix) else content


async def _expand_neighbors(
    session: AsyncSession, workspace_id: str, results: list[RetrievedChunk]
) -> None:
    """Append ord±1 chunk content around each hit (in place), deduplicating so
    no chunk's text appears twice across the returned results."""
    used: set[tuple[str, int]] = {(result.document_id, result.ord) for result in results}
    wanted: list[tuple[str, int]] = []
    for result in results:
        for neighbor_ord in (result.ord - 1, result.ord + 1):
            if neighbor_ord >= 0 and (result.document_id, neighbor_ord) not in used:
                wanted.append((result.document_id, neighbor_ord))
    if not wanted:
        return
    conditions = [
        and_(Chunk.document_id == document_id, Chunk.ord == neighbor_ord)
        for document_id, neighbor_ord in wanted
    ]
    rows = await session.execute(
        select(Chunk.document_id, Chunk.ord, Chunk.content).where(
            Chunk.workspace_id == workspace_id, or_(*conditions)
        )
    )
    neighbors = {(document_id, ord_): content for document_id, ord_, content in rows.all()}

    for result in results:
        prefix = f"# {result.title}\n\n"
        has_prefix = result.content.startswith(prefix)
        body_parts = [_strip_title_prefix(result.content, result.title)]
        before = (result.document_id, result.ord - 1)
        after = (result.document_id, result.ord + 1)
        if before in neighbors and before not in used:
            used.add(before)
            body_parts.insert(0, _strip_title_prefix(neighbors[before], result.title))
        if after in neighbors and after not in used:
            used.add(after)
            body_parts.append(_strip_title_prefix(neighbors[after], result.title))
        body = "\n\n".join(part for part in body_parts if part)
        result.content = prefix + body if has_prefix else body
