"""Help-center articles and their collections (served on the public portal)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime, uuid7
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class ArticleCollection(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "article_collections"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "locale", "slug", name="uq_article_collections_ws_locale_slug"
        ),
    )

    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    icon: Mapped[str | None] = mapped_column(String(20))  # emoji
    ord: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Language this collection is written in (`app.core.i18n` codes).
    locale: Mapped[str] = mapped_column(String(12), default="en", nullable=False, index=True)
    # Groups the same collection across locales. See `Article.translation_key`.
    translation_key: Mapped[str] = mapped_column(
        String(40), default=uuid7, nullable=False, index=True
    )


class Article(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "articles"
    __table_args__ = (
        # A slug is unique *within* a locale, so /de/passwort-zuruecksetzen and
        # /en/reset-password can coexist — and so can two locales that happen to
        # share a slug (product names, "faq").
        UniqueConstraint("workspace_id", "locale", "slug", name="uq_articles_ws_locale_slug"),
        # One variant per language per translation group.
        UniqueConstraint(
            "workspace_id",
            "translation_key",
            "locale",
            name="uq_articles_ws_translation_locale",
        ),
    )

    id: Mapped[str] = pk()
    # Language this article is written in (`app.core.i18n` codes).
    locale: Mapped[str] = mapped_column(String(12), default="en", nullable=False, index=True)
    # Shared id for "the same article in other languages". A new article gets a
    # fresh key, so it starts as a group of one and gaining a translation never
    # rewrites the original's identity or URL. Defaulted at the column rather
    # than only in the service, so constructing an Article directly — seeds,
    # fixtures, tests — cannot produce a row with no translation group.
    translation_key: Mapped[str] = mapped_column(
        String(40), default=uuid7, nullable=False, index=True
    )
    collection_id: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("article_collections.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)  # markdown
    # "draft" | "published"
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    author_id: Mapped[str | None] = mapped_column(GUID, ForeignKey("users.id", ondelete="SET NULL"))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
