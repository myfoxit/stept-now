"""Global search API: conversations, contacts, articles, and knowledge documents.

The empty router is pre-registered; add routes here, never touch the registry.
Sections are filtered by the caller's read permissions — a section the caller
cannot read is omitted entirely from the response.

The documents section is dialect-split. SQLite keeps the hybrid `search_chunks`
path. Postgres runs an as-you-type-tuned leg instead: full-text search with a
prefix (`tok:*`) match on the LAST token, a pg_trgm similarity fallback when FTS
finds fewer than 3 documents (typos), and a plain title ILIKE for 1–2 character
queries where FTS is useless. The response shape is identical on both dialects.
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import is_postgres
from app.core.deps import Db, Member, Principal
from app.core.permissions import Perm
from app.models.article import Article
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.knowledge import Chunk, Document
from app.rag.retrieval import search_chunks

router = APIRouter()

SNIPPET_LENGTH = 160
#: Queries at or below this length go to a title ILIKE instead of FTS.
SHORT_QUERY_MAX_CHARS = 2
#: When the FTS leg finds fewer documents than this, the trgm leg tops it up.
FTS_MIN_DOC_HITS = 3
#: pg_trgm similarity floor for the typo-fallback leg.
TRGM_SIMILARITY_THRESHOLD = 0.3
#: Chunk rows fetched per PG leg before deduplicating to documents.
CHUNK_SCAN_DEPTH = 30

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


class SearchConversation(BaseModel):
    id: str
    number: int
    subject: str | None = None
    contact_name: str | None = None
    last_activity_at: datetime


class SearchContact(BaseModel):
    id: str
    name: str
    email: str | None = None


class SearchArticle(BaseModel):
    id: str
    title: str
    slug: str


class SearchDocument(BaseModel):
    document_id: str
    title: str
    snippet: str
    score: float


class SearchResults(BaseModel):
    conversations: list[SearchConversation] | None = None
    contacts: list[SearchContact] | None = None
    articles: list[SearchArticle] | None = None
    documents: list[SearchDocument] | None = None


def allowed_sections(principal: Principal) -> set[str]:
    """Which search sections the caller may read."""
    sections: set[str] = set()
    if principal.has(Perm.CONVERSATIONS_READ):
        sections.add("conversations")
    if principal.has(Perm.CONTACTS_READ):
        sections.add("contacts")
    if principal.has(Perm.KNOWLEDGE_READ):
        sections.update({"articles", "documents"})
    return sections


async def _conversations(
    session: AsyncSession, workspace_id: str, q: str, limit: int
) -> list[SearchConversation]:
    pattern = f"%{q}%"
    clauses: list[ColumnElement[bool]] = [
        Conversation.subject.ilike(pattern),
        Contact.name.ilike(pattern),
        Contact.email.ilike(pattern),
    ]
    if q.isdigit():
        clauses.append(Conversation.number == int(q))
    rows = (
        await session.execute(
            select(Conversation, Contact)
            .join(Contact, Contact.id == Conversation.contact_id)
            .where(Conversation.workspace_id == workspace_id, or_(*clauses))
            .order_by(Conversation.last_activity_at.desc(), Conversation.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        SearchConversation(
            id=conv.id,
            number=conv.number,
            subject=conv.subject,
            contact_name=contact.name or None,
            last_activity_at=conv.last_activity_at,
        )
        for conv, contact in rows
    ]


async def _contacts(
    session: AsyncSession, workspace_id: str, q: str, limit: int
) -> list[SearchContact]:
    pattern = f"%{q}%"
    rows = (
        await session.execute(
            select(Contact)
            .where(
                Contact.workspace_id == workspace_id,
                or_(Contact.name.ilike(pattern), Contact.email.ilike(pattern)),
            )
            .order_by(Contact.last_seen_at.desc().nullslast(), Contact.id.desc())
            .limit(limit)
        )
    ).scalars()
    return [SearchContact(id=c.id, name=c.name, email=c.email) for c in rows]


async def _articles(
    session: AsyncSession, workspace_id: str, q: str, limit: int
) -> list[SearchArticle]:
    pattern = f"%{q}%"
    rows = (
        await session.execute(
            select(Article)
            .where(
                Article.workspace_id == workspace_id,
                or_(Article.title.ilike(pattern), Article.body.ilike(pattern)),
            )
            .order_by(Article.updated_at.desc(), Article.id.desc())
            .limit(limit)
        )
    ).scalars()
    return [SearchArticle(id=a.id, title=a.title, slug=a.slug) for a in rows]


def _snippet(content: str) -> str:
    body = content
    if body.startswith("# "):  # drop the injected "# Title" heading line
        _, _, rest = body.partition("\n")
        body = rest.lstrip("\n") or body
    return " ".join(body.split())[:SNIPPET_LENGTH]


async def _documents(
    session: AsyncSession, workspace_id: str, q: str, limit: int
) -> list[SearchDocument]:
    if is_postgres(session):
        return await _documents_pg(session, workspace_id, q, limit)
    chunks = await search_chunks(session, workspace_id, q, k=limit, expand_neighbors=False)
    seen: set[str] = set()
    out: list[SearchDocument] = []
    for chunk in chunks:  # already ordered best-first
        if chunk.document_id in seen:
            continue
        seen.add(chunk.document_id)
        out.append(
            SearchDocument(
                document_id=chunk.document_id,
                title=chunk.title,
                snippet=_snippet(chunk.content),
                score=chunk.score,
            )
        )
        if len(out) >= limit:
            break
    return out


# --- Postgres document leg (as-you-type: prefix FTS → trgm fallback → ILIKE) --


def prefix_tsquery(q: str) -> str | None:
    """`to_tsquery` input with a prefix match on the last token.

    "install the wid" → "install & the & wid:*" — the english config drops the
    stopword, and `wid:*` matches "widget", which is what makes a search box
    useful mid-word. Tokens are alphanumeric-only so user input can never break
    tsquery syntax.
    """
    tokens = _TOKEN_RE.findall(q.lower())
    if not tokens:
        return None
    return " & ".join([*tokens[:-1], f"{tokens[-1]}:*"])


def _dedupe_rows(rows: list, limit: int) -> list[SearchDocument]:
    """(document_id, content, title, score) chunk rows → best-chunk-per-document."""
    seen: set[str] = set()
    out: list[SearchDocument] = []
    for document_id, content, title, score in rows:
        doc_id = str(document_id)  # text() bypasses the GUID decoder on PG
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(
            SearchDocument(
                document_id=doc_id,
                title=title,
                snippet=_snippet(content),
                score=round(float(score), 6),
            )
        )
        if len(out) >= limit:
            break
    return out


async def _documents_pg(
    session: AsyncSession, workspace_id: str, q: str, limit: int
) -> list[SearchDocument]:
    if len(q) <= SHORT_QUERY_MAX_CHARS:
        return await _documents_pg_titles(session, workspace_id, q, limit)
    out = await _documents_pg_fts(session, workspace_id, q, limit)
    if len(out) < FTS_MIN_DOC_HITS:
        # Thin FTS result → top up with near-miss (typo) matches, never
        # displacing a real FTS hit.
        seen = {item.document_id for item in out}
        for item in await _documents_pg_trgm(session, workspace_id, q, limit):
            if len(out) >= limit:
                break
            if item.document_id in seen:
                continue
            seen.add(item.document_id)
            out.append(item)
    return out[:limit]


async def _documents_pg_fts(
    session: AsyncSession, workspace_id: str, q: str, limit: int
) -> list[SearchDocument]:
    tsq = prefix_tsquery(q)
    if tsq is None:
        return []
    stmt = text(
        """
        SELECT c.document_id, c.content, d.title,
               ts_rank_cd(to_tsvector('english', c.content),
                          to_tsquery('english', :tsq)) AS rank
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.workspace_id = :ws
          AND d.ai_searchable = TRUE
          AND to_tsvector('english', c.content) @@ to_tsquery('english', :tsq)
        ORDER BY rank DESC, c.id
        LIMIT :depth
        """
    )
    rows = (
        await session.execute(stmt, {"ws": workspace_id, "tsq": tsq, "depth": CHUNK_SCAN_DEPTH})
    ).all()
    return _dedupe_rows(list(rows), limit)


async def _documents_pg_trgm(
    session: AsyncSession, workspace_id: str, q: str, limit: int
) -> list[SearchDocument]:
    """Typo tolerance: trigram similarity against chunk content."""
    stmt = text(
        """
        SELECT c.document_id, c.content, d.title, similarity(c.content, :q) AS sim
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.workspace_id = :ws
          AND d.ai_searchable = TRUE
          AND similarity(c.content, :q) > :threshold
        ORDER BY sim DESC, c.id
        LIMIT :depth
        """
    )
    rows = (
        await session.execute(
            stmt,
            {
                "ws": workspace_id,
                "q": q,
                "threshold": TRGM_SIMILARITY_THRESHOLD,
                "depth": CHUNK_SCAN_DEPTH,
            },
        )
    ).all()
    return _dedupe_rows(list(rows), limit)


async def _documents_pg_titles(
    session: AsyncSession, workspace_id: str, q: str, limit: int
) -> list[SearchDocument]:
    """1–2 character queries: FTS/trgm are noise at that length; title ILIKE
    behaves like every other section's short-query behavior."""
    rows = (
        await session.execute(
            select(Document.id, Document.title)
            .where(
                Document.workspace_id == workspace_id,
                Document.ai_searchable.is_(True),
                Document.title.ilike(f"%{q}%"),
            )
            .order_by(Document.updated_at.desc(), Document.id.desc())
            .limit(limit)
        )
    ).all()
    if not rows:
        return []
    first_chunks: dict[str, str] = {
        str(document_id): content
        for document_id, content in (
            await session.execute(
                select(Chunk.document_id, Chunk.content).where(
                    Chunk.workspace_id == workspace_id,
                    Chunk.document_id.in_([document_id for document_id, _ in rows]),
                    Chunk.ord == 0,
                )
            )
        ).all()
    }
    return [
        SearchDocument(
            document_id=document_id,
            title=title,
            snippet=_snippet(first_chunks.get(document_id, "")),
            score=0.0,
        )
        for document_id, title in rows
    ]


@router.get("/search", response_model=SearchResults, response_model_exclude_none=True)
async def global_search(
    principal: Member,
    session: Db,
    q: str = Query("", max_length=200),
    limit: int = Query(5, ge=1, le=50),
) -> SearchResults:
    sections = allowed_sections(principal)
    workspace_id = principal.workspace.id
    query = q.strip()

    results = SearchResults()
    if "conversations" in sections:
        results.conversations = (
            await _conversations(session, workspace_id, query, limit) if query else []
        )
    if "contacts" in sections:
        results.contacts = await _contacts(session, workspace_id, query, limit) if query else []
    if "articles" in sections:
        results.articles = await _articles(session, workspace_id, query, limit) if query else []
    if "documents" in sections:
        results.documents = await _documents(session, workspace_id, query, limit) if query else []
    return results
