"""Help-center article schemas (admin CRUD + public portal payloads)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=500)
    icon: str | None = Field(None, max_length=20)
    ord: int = 0


class CollectionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=500)
    icon: str | None = Field(None, max_length=20)
    ord: int | None = None


class CollectionOut(ORMModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    icon: str | None = None
    ord: int
    created_at: datetime
    updated_at: datetime


class ArticleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=400)
    slug: str | None = Field(None, min_length=1, max_length=120)
    body: str = ""
    collection_id: str | None = None


class ArticleUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=400)
    slug: str | None = Field(None, min_length=1, max_length=120)
    body: str | None = None
    collection_id: str | None = None


class ArticleListItem(ORMModel):
    id: str
    collection_id: str | None = None
    title: str
    slug: str
    status: str
    author_id: str | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ArticleOut(ArticleListItem):
    body: str
    meta: dict[str, Any] = Field(default_factory=dict)


# --- public portal ----------------------------------------------------------


class PortalWorkspaceOut(BaseModel):
    name: str
    logo_url: str | None = None


class PortalArticleRef(BaseModel):
    title: str
    slug: str


class PortalCollectionOut(BaseModel):
    name: str
    slug: str
    icon: str | None = None
    description: str | None = None
    articles: list[PortalArticleRef]


class PortalHomeOut(BaseModel):
    workspace: PortalWorkspaceOut
    collections: list[PortalCollectionOut]


class PortalCollectionRef(BaseModel):
    name: str
    slug: str


class PortalArticleOut(BaseModel):
    title: str
    body: str
    collection: PortalCollectionRef | None = None
    published_at: datetime | None = None
