"""Widget public API: help-center browse + search.

Empty query returns the published collections tree; a query runs the knowledge
base's hybrid search restricted to the auto-managed "articles" source and maps
the hits back to their articles. Scoped to the widget's workspace via the
contact token.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.widget.deps import WidgetAuth
from app.core.deps import Db
from app.core.i18n import normalize_locale
from app.models.article import Article
from app.models.knowledge import Document
from app.rag.retrieval import search_chunks
from app.schemas.articles import PortalArticleOut, PortalCollectionOut
from app.services import articles as articles_service
from app.services.knowledge import get_or_create_articles_source
from app.services.search_analytics import record_search

router = APIRouter()

SEARCH_DEPTH = 10
SNIPPET_LENGTH = 200


class ArticleSearchResult(BaseModel):
    title: str
    slug: str
    snippet: str


class WidgetArticlesResponse(BaseModel):
    collections: list[PortalCollectionOut] = []
    results: list[ArticleSearchResult] = []


def _snippet(content: str, title: str) -> str:
    prefix = f"# {title}\n\n"
    body = content[len(prefix) :] if content.startswith(prefix) else content
    body = " ".join(body.split())
    return body[:SNIPPET_LENGTH]


async def _search_articles(
    session: AsyncSession, workspace_id: str, query: str, locale: str | None = None
) -> list[ArticleSearchResult]:
    source = await get_or_create_articles_source(session, workspace_id)
    chunks = await search_chunks(
        session,
        workspace_id,
        query,
        k=SEARCH_DEPTH,
        source_ids=[source.id],
        expand_neighbors=False,
        locale=locale,
    )
    await record_search(
        session,
        workspace_id,
        query=query,
        source="widget",
        results_count=len(chunks),
        top_score=chunks[0].score if chunks else None,
    )
    if not chunks:
        return []
    document_ids = {chunk.document_id for chunk in chunks}
    documents = (
        (await session.execute(select(Document).where(Document.id.in_(document_ids))))
        .scalars()
        .all()
    )
    article_id_by_document = {doc.id: doc.meta.get("article_id") for doc in documents}

    results: list[ArticleSearchResult] = []
    seen: set[str] = set()
    for chunk in chunks:
        article_id = article_id_by_document.get(chunk.document_id)
        if not article_id or article_id in seen:
            continue
        article = await session.get(Article, article_id)
        if article is None or article.workspace_id != workspace_id or article.status != "published":
            continue
        seen.add(article_id)
        results.append(
            ArticleSearchResult(
                title=article.title,
                slug=article.slug,
                snippet=_snippet(chunk.content, article.title),
            )
        )
    return results


@router.get("/articles", response_model=WidgetArticlesResponse)
async def articles(
    principal: WidgetAuth, session: Db, query: str = "", locale: str = ""
) -> WidgetArticlesResponse:
    # An explicit `?locale=` is the visitor using the help center's own language
    # switcher; otherwise serve the language we have learned they write in.
    reader_locale = normalize_locale(locale) or principal.contact.locale
    if query.strip():
        return WidgetArticlesResponse(
            results=await _search_articles(
                session, principal.workspace.id, query.strip(), reader_locale
            )
        )
    home = await articles_service.get_portal_home(
        session, principal.workspace.slug, locale=reader_locale
    )
    return WidgetArticlesResponse(collections=home.collections)


@router.get("/articles/{slug}", response_model=PortalArticleOut)
async def article(slug: str, principal: WidgetAuth, session: Db) -> PortalArticleOut:
    return await articles_service.get_portal_article(session, principal.workspace.slug, slug)
