"""Help-center collections + articles; publishing mirrors articles into the
knowledge base (Document + chunks in the auto-managed "articles" source) so
published help content is immediately searchable and citable by AI agents."""

from __future__ import annotations

import re
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow, uuid7
from app.core.errors import ConflictError, NotFoundError
from app.core.events import Actor
from app.core.i18n import DEFAULT_LOCALE, workspace_locale
from app.models.article import Article, ArticleCollection
from app.models.knowledge import Chunk, Document
from app.models.workspace import Workspace
from app.rag.ingestion import ingest_document
from app.schemas.articles import (
    PortalArticleOut,
    PortalArticleRef,
    PortalCollectionOut,
    PortalCollectionRef,
    PortalHomeOut,
    PortalWorkspaceOut,
)
from app.services import audit
from app.services.knowledge import get_or_create_articles_source

_UNSET = object()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:110] or "untitled"


async def _resolve_slug(
    session: AsyncSession,
    workspace_id: str,
    model: type[Article] | type[ArticleCollection],
    *,
    explicit: str | None,
    fallback: str,
    locale: str,
    exclude_id: str | None = None,
) -> str:
    """Explicit slugs must be free (409 otherwise); generated ones auto-dedupe.

    Scoped to one locale, matching the DB constraint: the German translation of
    "Reset your password" is entitled to the slug its own title generates, even
    if some English article already took the same string.
    """

    async def taken(candidate: str) -> bool:
        query = select(model.id).where(
            model.workspace_id == workspace_id,
            model.locale == locale,
            model.slug == candidate,
        )
        if exclude_id:
            query = query.where(model.id != exclude_id)
        return (await session.execute(query)).first() is not None

    if explicit is not None:
        slug = slugify(explicit)
        if await taken(slug):
            raise ConflictError(f"Slug '{slug}' is already in use")
        return slug
    base = slugify(fallback)
    slug = base
    suffix = 2
    while await taken(slug):
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------


async def list_collections(session: AsyncSession, workspace_id: str) -> list[ArticleCollection]:
    rows = await session.execute(
        select(ArticleCollection)
        .where(ArticleCollection.workspace_id == workspace_id)
        .order_by(ArticleCollection.ord, ArticleCollection.name)
    )
    return list(rows.scalars())


async def create_collection(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    name: str,
    slug: str | None = None,
    description: str | None = None,
    icon: str | None = None,
    ord: int = 0,
    locale: str = DEFAULT_LOCALE,
    translation_key: str | None = None,
) -> ArticleCollection:
    collection_id = uuid7()
    collection = ArticleCollection(
        id=collection_id,
        workspace_id=workspace_id,
        name=name.strip(),
        slug=await _resolve_slug(
            session, workspace_id, ArticleCollection, explicit=slug, fallback=name, locale=locale
        ),
        description=description,
        icon=icon,
        ord=ord,
        locale=locale,
        # No key given ⇒ this is an original, not a translation, so it starts
        # its own group keyed by its own id.
        translation_key=translation_key or collection_id,
    )
    session.add(collection)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="article.collection.create",
        target_type="article_collection",
        target_id=collection.id,
        meta={"name": collection.name},
    )
    return collection


async def get_collection(
    session: AsyncSession, workspace_id: str, collection_id: str
) -> ArticleCollection:
    collection = await session.get(ArticleCollection, collection_id)
    if collection is None or collection.workspace_id != workspace_id:
        raise NotFoundError("Collection not found")
    return collection


async def update_collection(
    session: AsyncSession,
    workspace_id: str,
    collection_id: str,
    *,
    actor: Actor,
    name: str | None = None,
    slug: str | None = None,
    description: str | None = None,
    icon: str | None = None,
    ord: int | None = None,
) -> ArticleCollection:
    collection = await get_collection(session, workspace_id, collection_id)
    if name is not None:
        collection.name = name.strip()
    if slug is not None:
        collection.slug = await _resolve_slug(
            session,
            workspace_id,
            ArticleCollection,
            explicit=slug,
            fallback=slug,
            locale=collection.locale,
            exclude_id=collection_id,
        )
    if description is not None:
        collection.description = description
    if icon is not None:
        collection.icon = icon
    if ord is not None:
        collection.ord = ord
    await session.flush()
    return collection


async def delete_collection(
    session: AsyncSession, workspace_id: str, collection_id: str, *, actor: Actor
) -> None:
    collection = await get_collection(session, workspace_id, collection_id)
    # Portable SET NULL (SQLite does not enforce FK actions by default).
    for article in (
        (await session.execute(select(Article).where(Article.collection_id == collection_id)))
        .scalars()
        .all()
    ):
        article.collection_id = None
    await session.delete(collection)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="article.collection.delete",
        target_type="article_collection",
        target_id=collection_id,
        meta={"name": collection.name},
    )


def _pick_locale_variants[T: (Article, ArticleCollection)](
    rows: Sequence[T], locale: str | None, default_locale: str
) -> list[T]:
    """One row per translation group: the reader's language, else the fallback.

    Preference order within a group is reader's locale → workspace default →
    English → whatever exists, so a group always yields exactly one row and the
    help center never shows the same article twice in two languages.
    """
    groups: dict[str, list[T]] = {}
    for row in rows:
        groups.setdefault(row.translation_key, []).append(row)

    preference = [code for code in (locale, default_locale, DEFAULT_LOCALE) if code]
    picked: list[T] = []
    for variants in groups.values():
        by_locale = {variant.locale: variant for variant in variants}
        chosen = next((by_locale[code] for code in preference if code in by_locale), variants[0])
        picked.append(chosen)
    return picked


# ---------------------------------------------------------------------------
# articles
# ---------------------------------------------------------------------------


async def list_articles(
    session: AsyncSession,
    workspace_id: str,
    *,
    collection_id: str | None = None,
    status: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Article], int]:
    query = select(Article).where(Article.workspace_id == workspace_id)
    count_query = (
        select(func.count()).select_from(Article).where(Article.workspace_id == workspace_id)
    )
    if collection_id:
        query = query.where(Article.collection_id == collection_id)
        count_query = count_query.where(Article.collection_id == collection_id)
    if status:
        query = query.where(Article.status == status)
        count_query = count_query.where(Article.status == status)
    total = (await session.execute(count_query)).scalar_one()
    rows = await session.execute(
        query.order_by(Article.updated_at.desc()).limit(limit).offset(offset)
    )
    return list(rows.scalars()), total


async def get_article(session: AsyncSession, workspace_id: str, article_id: str) -> Article:
    article = await session.get(Article, article_id)
    if article is None or article.workspace_id != workspace_id:
        raise NotFoundError("Article not found")
    return article


async def create_article(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    title: str,
    body: str = "",
    slug: str | None = None,
    collection_id: str | None = None,
    locale: str = DEFAULT_LOCALE,
    translation_key: str | None = None,
) -> Article:
    if collection_id is not None:
        await get_collection(session, workspace_id, collection_id)
    if translation_key is not None:
        existing = (
            await session.execute(
                select(Article.id).where(
                    Article.workspace_id == workspace_id,
                    Article.translation_key == translation_key,
                    Article.locale == locale,
                )
            )
        ).first()
        if existing is not None:
            raise ConflictError(f"This article already has a {locale} translation")
    article_id = uuid7()
    article = Article(
        id=article_id,
        workspace_id=workspace_id,
        collection_id=collection_id,
        title=title.strip(),
        slug=await _resolve_slug(
            session, workspace_id, Article, explicit=slug, fallback=title, locale=locale
        ),
        body=body,
        status="draft",
        author_id=actor.id if actor.type == "user" else None,
        locale=locale,
        translation_key=translation_key or article_id,
    )
    session.add(article)
    await session.flush()
    return article


async def update_article(
    session: AsyncSession,
    workspace_id: str,
    article_id: str,
    *,
    actor: Actor,
    title: str | None = None,
    slug: str | None = None,
    body: str | None = None,
    collection_id: object = _UNSET,
) -> Article:
    article = await get_article(session, workspace_id, article_id)
    if title is not None:
        article.title = title.strip()
    if slug is not None:
        article.slug = await _resolve_slug(
            session,
            workspace_id,
            Article,
            explicit=slug,
            fallback=slug,
            locale=article.locale,
            exclude_id=article_id,
        )
    if body is not None:
        article.body = body
    if collection_id is not _UNSET:
        if collection_id is not None:
            await get_collection(session, workspace_id, str(collection_id))
        article.collection_id = collection_id  # type: ignore[assignment]
    await session.flush()
    if article.status == "published":
        await _sync_published_article(session, article)  # keep search index fresh
    return article


async def _find_article_document(
    session: AsyncSession, source_id: str, article_id: str
) -> Document | None:
    rows = await session.execute(select(Document).where(Document.source_id == source_id))
    for document in rows.scalars():
        if document.meta.get("article_id") == article_id:
            return document
    return None


async def _portal_uri(session: AsyncSession, article: Article) -> str:
    workspace = await session.get(Workspace, article.workspace_id)
    workspace_slug = workspace.slug if workspace is not None else article.workspace_id
    return f"/portal/{workspace_slug}/articles/{article.slug}"


async def _sync_published_article(session: AsyncSession, article: Article) -> Document:
    """Upsert the article's Document and (re-)ingest inline — publish is
    synchronous so 'publish → searchable' holds without draining a queue."""
    source = await get_or_create_articles_source(session, article.workspace_id)
    document = await _find_article_document(session, source.id, article.id)
    uri = await _portal_uri(session, article)
    if document is None:
        document = Document(
            workspace_id=article.workspace_id,
            source_id=source.id,
            title=article.title,
            uri=uri,
            mime="text/markdown",
            status="processing",
            meta={"article_id": article.id, "locale": article.locale},
        )
        session.add(document)
        await session.flush()
    else:
        document.title = article.title
        document.uri = uri
        document.status = "processing"
        document.error = None
        document.meta = {**document.meta, "locale": article.locale}
    await ingest_document(session, document, article.body)
    return document


async def _remove_article_document(session: AsyncSession, article: Article) -> None:
    source = await get_or_create_articles_source(session, article.workspace_id)
    document = await _find_article_document(session, source.id, article.id)
    if document is not None:
        await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
        await session.delete(document)
        await session.flush()


async def publish_article(
    session: AsyncSession, workspace_id: str, article_id: str, *, actor: Actor
) -> Article:
    article = await get_article(session, workspace_id, article_id)
    article.status = "published"
    article.published_at = utcnow()
    await session.flush()
    await _sync_published_article(session, article)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="article.publish",
        target_type="article",
        target_id=article.id,
        meta={"title": article.title, "slug": article.slug},
    )
    return article


async def unpublish_article(
    session: AsyncSession, workspace_id: str, article_id: str, *, actor: Actor
) -> Article:
    article = await get_article(session, workspace_id, article_id)
    article.status = "draft"
    article.published_at = None
    await _remove_article_document(session, article)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="article.unpublish",
        target_type="article",
        target_id=article.id,
        meta={"title": article.title, "slug": article.slug},
    )
    return article


async def delete_article(
    session: AsyncSession, workspace_id: str, article_id: str, *, actor: Actor
) -> None:
    article = await get_article(session, workspace_id, article_id)
    await _remove_article_document(session, article)
    await session.delete(article)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="article.delete",
        target_type="article",
        target_id=article_id,
        meta={"title": article.title},
    )


# ---------------------------------------------------------------------------
# public portal
# ---------------------------------------------------------------------------


async def _workspace_by_slug(session: AsyncSession, workspace_slug: str) -> Workspace:
    workspace = (
        await session.execute(select(Workspace).where(Workspace.slug == workspace_slug))
    ).scalar_one_or_none()
    if workspace is None:
        raise NotFoundError("Help center not found")
    return workspace


async def get_portal_home(
    session: AsyncSession, workspace_slug: str, *, locale: str | None = None
) -> PortalHomeOut:
    """The help-center index, in the reader's language where one exists.

    Falls back per translation group rather than wholesale: a workspace that has
    translated three of its forty articles shows those three in German and the
    rest in its default language, instead of showing a near-empty German help
    center or ignoring the translations it does have.
    """
    workspace = await _workspace_by_slug(session, workspace_slug)
    default_locale = workspace_locale(workspace.settings) or DEFAULT_LOCALE
    all_published = (
        (
            await session.execute(
                select(Article)
                .where(Article.workspace_id == workspace.id, Article.status == "published")
                .order_by(Article.title)
            )
        )
        .scalars()
        .all()
    )
    published = _pick_locale_variants(all_published, locale, default_locale)
    by_collection: dict[str | None, list[PortalArticleRef]] = {}
    for article in published:
        by_collection.setdefault(article.collection_id, []).append(
            PortalArticleRef(title=article.title, slug=article.slug)
        )

    collections_out: list[PortalCollectionOut] = []
    all_collections = await list_collections(session, workspace.id)
    for collection in _pick_locale_variants(all_collections, locale, default_locale):
        articles = by_collection.pop(collection.id, [])
        if articles:
            collections_out.append(
                PortalCollectionOut(
                    name=collection.name,
                    slug=collection.slug,
                    icon=collection.icon,
                    description=collection.description,
                    articles=articles,
                )
            )
    uncollected = by_collection.pop(None, [])
    if uncollected:
        collections_out.append(
            PortalCollectionOut(
                name="Other", slug="other", icon=None, description=None, articles=uncollected
            )
        )
    return PortalHomeOut(
        workspace=PortalWorkspaceOut(name=workspace.name, logo_url=workspace.logo_url),
        collections=collections_out,
    )


async def get_portal_article(
    session: AsyncSession, workspace_slug: str, article_slug: str
) -> PortalArticleOut:
    workspace = await _workspace_by_slug(session, workspace_slug)
    article = (
        await session.execute(
            select(Article).where(
                Article.workspace_id == workspace.id,
                Article.slug == article_slug,
                Article.status == "published",
            )
        )
    ).scalar_one_or_none()
    if article is None:
        raise NotFoundError("Article not found")
    collection_ref = None
    if article.collection_id is not None:
        collection = await session.get(ArticleCollection, article.collection_id)
        if collection is not None:
            collection_ref = PortalCollectionRef(name=collection.name, slug=collection.slug)
    return PortalArticleOut(
        title=article.title,
        body=article.body,
        collection=collection_ref,
        published_at=article.published_at,
    )
