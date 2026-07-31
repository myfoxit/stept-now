"""Knowledge base schemas: sources, documents, chunks, hybrid search."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class SourceCreate(BaseModel):
    # "articles" is excluded on purpose: that source is managed automatically.
    type: Literal["files", "urls", "text"]
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)


class SourceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None


class SourceOut(ORMModel):
    id: str
    type: str
    name: str
    config: dict[str, Any]
    status: str
    error: str | None = None
    last_synced_at: datetime | None = None
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class TextDocumentCreate(BaseModel):
    """JSON body for pasting text/markdown straight into a source."""

    title: str = Field(min_length=1, max_length=400)
    content: str = Field(min_length=1)


class DocumentOut(ORMModel):
    id: str
    source_id: str
    title: str
    uri: str | None = None
    mime: str | None = None
    content_hash: str | None = None
    status: str
    error: str | None = None
    token_count: int
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ChunkPreviewOut(ORMModel):
    id: str
    ord: int
    content: str
    token_count: int
    meta: dict[str, Any]


class DocumentDetailOut(DocumentOut):
    chunks: list[ChunkPreviewOut] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=8, ge=1, le=50)
    source_ids: list[str] | None = None


class RetrievedChunkOut(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    score: float
    title: str
    url: str | None = None
    ord: int


class SearchResponse(BaseModel):
    results: list[RetrievedChunkOut]
    latency_ms: float
