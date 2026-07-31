"""Knowledge sources, documents, and hybrid search (service layer)."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import BadRequestError, NotFoundError, PayloadTooLargeError, ValidationFailure
from app.core.events import Actor
from app.core.queue import enqueue
from app.core.storage import get_storage
from app.models.knowledge import Chunk, Document, KnowledgeSource
from app.rag import tasks as rag_tasks  # noqa: F401  (registers queue tasks on import)
from app.rag.parsers import ParseError, extract, normalize_mime
from app.rag.retrieval import search_chunks
from app.schemas.knowledge import RetrievedChunkOut, SearchResponse, SourceOut
from app.services import audit

ARTICLES_SOURCE_NAME = "Help center articles"


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


def _validate_config(source_type: str, config: dict[str, Any]) -> dict[str, Any]:
    if source_type == "urls":
        urls = config.get("urls")
        if not isinstance(urls, list) or not urls:
            raise ValidationFailure("urls sources need config.urls: a non-empty list of URLs")
        for url in urls:
            if not isinstance(url, str) or not url.strip().startswith(("http://", "https://")):
                raise ValidationFailure(f"Invalid URL in config.urls: {url!r}")
        config = {**config, "urls": [url.strip() for url in urls]}
    boost = config.get("boost")
    if boost is not None and not isinstance(boost, (int, float)):
        raise ValidationFailure("config.boost must be a number")
    return config


async def _document_counts(session: AsyncSession, workspace_id: str) -> dict[str, int]:
    rows = await session.execute(
        select(Document.source_id, func.count())
        .where(Document.workspace_id == workspace_id)
        .group_by(Document.source_id)
    )
    return dict(rows.all())  # type: ignore[arg-type]


def source_out(source: KnowledgeSource, document_count: int) -> SourceOut:
    out = SourceOut.model_validate(source)
    out.document_count = document_count
    return out


async def list_sources(session: AsyncSession, workspace_id: str) -> list[SourceOut]:
    counts = await _document_counts(session, workspace_id)
    rows = await session.execute(
        select(KnowledgeSource)
        .where(KnowledgeSource.workspace_id == workspace_id)
        .order_by(KnowledgeSource.created_at)
    )
    return [source_out(source, counts.get(source.id, 0)) for source in rows.scalars()]


async def create_source(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    type: str,
    name: str,
    config: dict[str, Any] | None = None,
) -> KnowledgeSource:
    if type == "articles":
        raise BadRequestError("The articles source is managed automatically")
    source = KnowledgeSource(
        workspace_id=workspace_id,
        type=type,
        name=name.strip(),
        config=_validate_config(type, config or {}),
    )
    session.add(source)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="knowledge.source.create",
        target_type="knowledge_source",
        target_id=source.id,
        meta={"name": source.name, "type": source.type},
    )
    return source


async def get_source(session: AsyncSession, workspace_id: str, source_id: str) -> KnowledgeSource:
    source = await session.get(KnowledgeSource, source_id)
    if source is None or source.workspace_id != workspace_id:
        raise NotFoundError("Source not found")
    return source


async def count_documents(session: AsyncSession, source_id: str) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(Document).where(Document.source_id == source_id)
        )
    ).scalar_one()


async def update_source(
    session: AsyncSession,
    workspace_id: str,
    source_id: str,
    *,
    actor: Actor,
    name: str | None = None,
    config: dict[str, Any] | None = None,
) -> KnowledgeSource:
    source = await get_source(session, workspace_id, source_id)
    if name is not None:
        source.name = name.strip()
    if config is not None:
        source.config = _validate_config(source.type, config)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="knowledge.source.update",
        target_type="knowledge_source",
        target_id=source.id,
        meta={"name": source.name},
    )
    return source


async def delete_source(
    session: AsyncSession, workspace_id: str, source_id: str, *, actor: Actor
) -> None:
    source = await get_source(session, workspace_id, source_id)
    document_ids = select(Document.id).where(Document.source_id == source_id)
    await session.execute(delete(Chunk).where(Chunk.document_id.in_(document_ids)))
    await session.execute(delete(Document).where(Document.source_id == source_id))
    await session.delete(source)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="knowledge.source.delete",
        target_type="knowledge_source",
        target_id=source_id,
        meta={"name": source.name},
    )


async def trigger_sync(
    session: AsyncSession, workspace_id: str, source_id: str, *, actor: Actor
) -> KnowledgeSource:
    source = await get_source(session, workspace_id, source_id)
    source.status = "syncing"
    source.error = None
    await session.flush()
    await enqueue("sync_source", source_id=source.id)
    return source


async def get_or_create_articles_source(
    session: AsyncSession, workspace_id: str
) -> KnowledgeSource:
    """Singleton per workspace mirroring published help-center articles."""
    source = (
        await session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.workspace_id == workspace_id, KnowledgeSource.type == "articles"
            )
        )
    ).scalar_one_or_none()
    if source is None:
        source = KnowledgeSource(
            workspace_id=workspace_id, type="articles", name=ARTICLES_SOURCE_NAME
        )
        session.add(source)
        await session.flush()
    return source


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------


async def add_document_from_file(
    session: AsyncSession,
    workspace_id: str,
    source: KnowledgeSource,
    *,
    actor: Actor,
    filename: str,
    data: bytes,
    content_type: str | None,
) -> Document:
    if source.type == "articles":
        raise BadRequestError("The articles source is managed automatically")
    settings = get_settings()
    if len(data) > settings.upload_limit_bytes:
        raise PayloadTooLargeError(f"Files are limited to {settings.max_upload_mb} MB")
    if not data:
        raise BadRequestError("Empty file")
    try:
        parsed = extract(filename, data, content_type)  # validate before accepting
    except ParseError as exc:
        raise BadRequestError(str(exc)) from exc
    stored = await get_storage().save(filename, data)
    document = Document(
        workspace_id=workspace_id,
        source_id=source.id,
        title=parsed.title,
        uri=stored.key,
        mime=normalize_mime(filename, content_type),
        status="pending",
        meta={"filename": filename, "size": stored.size},
    )
    session.add(document)
    await session.flush()
    await enqueue("ingest_document", document_id=document.id)
    return document


async def add_document_from_text(
    session: AsyncSession,
    workspace_id: str,
    source: KnowledgeSource,
    *,
    actor: Actor,
    title: str,
    content: str,
) -> Document:
    if source.type == "articles":
        raise BadRequestError("The articles source is managed automatically")
    title = title.strip()
    filename = f"{title or 'document'}.md"
    stored = await get_storage().save(filename, content.encode("utf-8"))
    document = Document(
        workspace_id=workspace_id,
        source_id=source.id,
        title=title,
        uri=stored.key,
        mime="text/markdown",
        status="pending",
        meta={"filename": filename, "size": stored.size},
    )
    session.add(document)
    await session.flush()
    await enqueue("ingest_document", document_id=document.id)
    return document


async def list_documents(
    session: AsyncSession,
    workspace_id: str,
    *,
    source_id: str | None = None,
    status: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[Document], int]:
    query = select(Document).where(Document.workspace_id == workspace_id)
    count_query = (
        select(func.count()).select_from(Document).where(Document.workspace_id == workspace_id)
    )
    if source_id:
        query = query.where(Document.source_id == source_id)
        count_query = count_query.where(Document.source_id == source_id)
    if status:
        query = query.where(Document.status == status)
        count_query = count_query.where(Document.status == status)
    total = (await session.execute(count_query)).scalar_one()
    rows = await session.execute(
        query.order_by(Document.created_at.desc()).limit(limit).offset(offset)
    )
    return list(rows.scalars()), total


async def get_document(session: AsyncSession, workspace_id: str, document_id: str) -> Document:
    document = await session.get(Document, document_id)
    if document is None or document.workspace_id != workspace_id:
        raise NotFoundError("Document not found")
    return document


async def get_document_chunks(
    session: AsyncSession, document_id: str, *, limit: int = 5
) -> list[Chunk]:
    rows = await session.execute(
        select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.ord).limit(limit)
    )
    return list(rows.scalars())


async def delete_document(
    session: AsyncSession, workspace_id: str, document_id: str, *, actor: Actor
) -> None:
    document = await get_document(session, workspace_id, document_id)
    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    await session.delete(document)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="knowledge.document.delete",
        target_type="document",
        target_id=document_id,
        meta={"title": document.title},
    )


async def retry_document(
    session: AsyncSession, workspace_id: str, document_id: str, *, actor: Actor
) -> Document:
    document = await get_document(session, workspace_id, document_id)
    document.status = "pending"
    document.error = None
    await session.flush()
    await enqueue("ingest_document", document_id=document.id)
    return document


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def search(
    session: AsyncSession,
    workspace_id: str,
    query: str,
    *,
    k: int = 8,
    source_ids: list[str] | None = None,
) -> SearchResponse:
    started = time.perf_counter()
    results = await search_chunks(session, workspace_id, query, k=k, source_ids=source_ids)
    latency_ms = (time.perf_counter() - started) * 1000
    return SearchResponse(
        results=[
            RetrievedChunkOut(
                chunk_id=result.chunk_id,
                document_id=result.document_id,
                content=result.content,
                score=result.score,
                title=result.title,
                url=result.url,
                ord=result.ord,
            )
            for result in results
        ],
        latency_ms=round(latency_ms, 2),
    )
