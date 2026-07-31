"""Knowledge base: sources → documents → chunks (the RAG data model).

Chunks live in the "chunks" table with a `content` text column — the Postgres
bootstrap in `app.core.db.ensure_pg_indexes` creates a GIN expression index on
`to_tsvector('english', content)` for the hybrid-search lexical leg.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, EmbeddingVector, PortableJSON, UTCDateTime, utcnow
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class KnowledgeSource(TimestampMixin, WorkspaceScopedMixin, Base):
    """A grouping of documents: uploaded files, crawled URLs, pasted text,
    connector-synced content (sitemap/crawl/github/notion), or the
    auto-managed singleton that mirrors published help-center articles."""

    __tablename__ = "knowledge_sources"

    id: Mapped[str] = pk()
    # "files" | "urls" | "text" | "articles" | "sitemap" | "crawl" | "github" | "notion"
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Per-type config, e.g. {"urls": [...]} for the urls type; optional "boost"
    # float in [0.5, 2.0] applied multiplicatively at retrieval time; optional
    # "refresh_minutes" int >= 5 enabling scheduled re-sync.
    config: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # Fernet-encrypted JSON credentials (GitHub/Notion tokens), mirroring
    # Inbox.secrets_encrypted — write-only via the API, never returned.
    secrets_encrypted: Mapped[str | None] = mapped_column(Text)
    # "idle" | "syncing" | "error"
    status: Mapped[str] = mapped_column(String(20), default="idle", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Document(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_ws_source", "workspace_id", "source_id"),)

    id: Mapped[str] = pk()
    source_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    # URL (urls type), storage key (files/text), or portal path (articles).
    uri: Mapped[str | None] = mapped_column(String(1000))
    mime: Mapped[str | None] = mapped_column(String(120))
    # sha256 of the extracted text — unchanged hash skips re-chunk/re-embed.
    content_hash: Mapped[str | None] = mapped_column(String(64))
    # "pending" | "processing" | "indexed" | "failed"
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


class Chunk(WorkspaceScopedMixin, Base):
    """One embedded retrieval unit. Table/column names are load-bearing:
    "chunks"."content" is targeted by the core Postgres FTS expression index."""

    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_ws_doc_ord", "workspace_id", "document_id", "ord"),)

    id: Mapped[str] = pk()
    document_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector)
    # {"title": ..., "url": ... | None, "headings": [...]}
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
