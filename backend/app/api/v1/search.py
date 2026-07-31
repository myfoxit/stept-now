"""Global search API: conversations, contacts, articles, and knowledge documents.

The empty router is pre-registered; add routes here, never touch the registry.
Sections are filtered by the caller's read permissions — a section the caller
cannot read is omitted entirely from the response.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Db, Member, Principal
from app.core.permissions import Perm
from app.models.article import Article
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.rag.retrieval import search_chunks

router = APIRouter()

SNIPPET_LENGTH = 160


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
