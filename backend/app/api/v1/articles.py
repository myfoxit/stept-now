"""Help-center articles API (admin side; the public portal lives in
app/api/portal.py). Collection routes are registered before /articles/{id}."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, require_perm
from app.core.events import Actor
from app.core.pagination import OffsetPage, clamp_limit
from app.core.permissions import Perm
from app.schemas.articles import (
    ArticleCreate,
    ArticleListItem,
    ArticleOut,
    ArticleUpdate,
    CollectionCreate,
    CollectionOut,
    CollectionUpdate,
)
from app.schemas.common import Msg
from app.services import articles as articles_service

router = APIRouter()

KnowledgeRead = Depends(require_perm(Perm.KNOWLEDGE_READ))
KnowledgeWrite = Depends(require_perm(Perm.KNOWLEDGE_WRITE))


def _actor(principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


# --- collections (registered before /articles/{article_id}) -----------------


@router.get(
    "/articles/collections", response_model=list[CollectionOut], dependencies=[KnowledgeRead]
)
async def list_collections(principal: Member, session: Db):
    collections = await articles_service.list_collections(session, principal.workspace.id)
    return [CollectionOut.model_validate(collection) for collection in collections]


@router.post(
    "/articles/collections",
    response_model=CollectionOut,
    status_code=201,
    dependencies=[KnowledgeWrite],
)
async def create_collection(body: CollectionCreate, principal: Member, session: Db):
    collection = await articles_service.create_collection(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        name=body.name,
        slug=body.slug,
        description=body.description,
        icon=body.icon,
        ord=body.ord,
    )
    return CollectionOut.model_validate(collection)


@router.patch(
    "/articles/collections/{collection_id}",
    response_model=CollectionOut,
    dependencies=[KnowledgeWrite],
)
async def update_collection(
    collection_id: str, body: CollectionUpdate, principal: Member, session: Db
):
    collection = await articles_service.update_collection(
        session,
        principal.workspace.id,
        collection_id,
        actor=_actor(principal),
        name=body.name,
        slug=body.slug,
        description=body.description,
        icon=body.icon,
        ord=body.ord,
    )
    return CollectionOut.model_validate(collection)


@router.delete(
    "/articles/collections/{collection_id}", response_model=Msg, dependencies=[KnowledgeWrite]
)
async def delete_collection(collection_id: str, principal: Member, session: Db):
    await articles_service.delete_collection(
        session, principal.workspace.id, collection_id, actor=_actor(principal)
    )
    return Msg(message="Collection deleted")


# --- articles ---------------------------------------------------------------


@router.get("/articles", response_model=OffsetPage[ArticleListItem], dependencies=[KnowledgeRead])
async def list_articles(
    principal: Member,
    session: Db,
    collection_id: str | None = None,
    status: str | None = None,
    limit: int | None = None,
    offset: int = 0,
):
    limit = clamp_limit(limit)
    articles, total = await articles_service.list_articles(
        session,
        principal.workspace.id,
        collection_id=collection_id,
        status=status,
        limit=limit,
        offset=max(offset, 0),
    )
    return OffsetPage(
        items=[ArticleListItem.model_validate(article) for article in articles],
        total=total,
        limit=limit,
        offset=max(offset, 0),
    )


@router.post("/articles", response_model=ArticleOut, status_code=201, dependencies=[KnowledgeWrite])
async def create_article(body: ArticleCreate, principal: Member, session: Db):
    article = await articles_service.create_article(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        title=body.title,
        body=body.body,
        slug=body.slug,
        collection_id=body.collection_id,
    )
    return ArticleOut.model_validate(article)


@router.get("/articles/{article_id}", response_model=ArticleOut, dependencies=[KnowledgeRead])
async def get_article(article_id: str, principal: Member, session: Db):
    article = await articles_service.get_article(session, principal.workspace.id, article_id)
    return ArticleOut.model_validate(article)


@router.patch("/articles/{article_id}", response_model=ArticleOut, dependencies=[KnowledgeWrite])
async def update_article(article_id: str, body: ArticleUpdate, principal: Member, session: Db):
    kwargs: dict = {}
    if "collection_id" in body.model_fields_set:
        kwargs["collection_id"] = body.collection_id
    article = await articles_service.update_article(
        session,
        principal.workspace.id,
        article_id,
        actor=_actor(principal),
        title=body.title,
        slug=body.slug,
        body=body.body,
        **kwargs,
    )
    return ArticleOut.model_validate(article)


@router.delete("/articles/{article_id}", response_model=Msg, dependencies=[KnowledgeWrite])
async def delete_article(article_id: str, principal: Member, session: Db):
    await articles_service.delete_article(
        session, principal.workspace.id, article_id, actor=_actor(principal)
    )
    return Msg(message="Article deleted")


@router.post(
    "/articles/{article_id}/publish", response_model=ArticleOut, dependencies=[KnowledgeWrite]
)
async def publish_article(article_id: str, principal: Member, session: Db):
    article = await articles_service.publish_article(
        session, principal.workspace.id, article_id, actor=_actor(principal)
    )
    return ArticleOut.model_validate(article)


@router.post(
    "/articles/{article_id}/unpublish", response_model=ArticleOut, dependencies=[KnowledgeWrite]
)
async def unpublish_article(article_id: str, principal: Member, session: Db):
    article = await articles_service.unpublish_article(
        session, principal.workspace.id, article_id, actor=_actor(principal)
    )
    return ArticleOut.model_validate(article)
