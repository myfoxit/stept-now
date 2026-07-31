"""Document ingestion: text → chunks → embeddings → indexed rows.

`ingest_document` is the single write path into the chunk store; it is called
inline (article publish, URL sync, seed) and from the background task wrappers
in `app.rag.tasks`.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import embed_texts
from app.core.db import utcnow
from app.core.events import Event, EventNames, emit
from app.models.knowledge import Chunk, Document
from app.rag.chunker import chunk_text

EMBED_BATCH_SIZE = 64


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunk_url(document: Document) -> str | None:
    """Citation link carried on every chunk: only real links, never storage keys."""
    uri = document.uri or ""
    if uri.startswith(("http://", "https://", "/")):
        return uri
    return None


async def ingest_document(session: AsyncSession, document: Document, text: str) -> int:
    """Chunk + embed `text` into the chunks table for `document`.

    Skips all work when the content hash is unchanged and chunks already exist
    (chunk ids stay stable). Returns the number of chunks now indexed. Marks
    the document "indexed" and emits `document.indexed`.
    """
    digest = content_hash(text)
    if document.content_hash == digest:
        existing = (
            await session.execute(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)
            )
        ).scalar_one()
        if existing:  # unchanged content, chunks present → chunk ids stay stable
            document.status = "indexed"
            document.error = None
            await session.flush()
            return existing

    drafts = chunk_text(text, title=document.title)
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))

    url = _chunk_url(document)
    embeddings: list[list[float]] = []
    for start in range(0, len(drafts), EMBED_BATCH_SIZE):
        batch = drafts[start : start + EMBED_BATCH_SIZE]
        embeddings.extend(
            await embed_texts(session, document.workspace_id, [draft.content for draft in batch])
        )

    for draft, embedding in zip(drafts, embeddings, strict=True):
        session.add(
            Chunk(
                workspace_id=document.workspace_id,
                document_id=document.id,
                ord=draft.ord,
                content=draft.content,
                embedding=embedding,
                meta={"title": document.title, "url": url, "headings": draft.headings},
                token_count=draft.token_count,
            )
        )

    document.content_hash = digest
    document.status = "indexed"
    document.error = None
    document.token_count = sum(draft.token_count for draft in drafts)
    document.updated_at = utcnow()
    await session.flush()
    await emit(
        session,
        Event(
            name=EventNames.DOCUMENT_INDEXED,
            workspace_id=document.workspace_id,
            payload={
                "document_id": document.id,
                "source_id": document.source_id,
                "chunk_count": len(drafts),
            },
        ),
    )
    return len(drafts)
