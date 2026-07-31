"""Background tasks for the RAG pipeline.

- ingest_document {document_id}: (re)load the raw content, extract, ingest.
- sync_source {source_id}: for "urls" sources, fetch each configured URL
  (10s timeout, 2MB cap, text/html only) and ingest inline; other source
  types re-ingest their non-indexed documents. Failures mark the document/
  source as errored — the task itself never raises for content problems.

Importing this module registers the tasks (`app.services.knowledge` imports it,
which the API package pulls in at startup).
"""

from __future__ import annotations

import asyncio
import weakref

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope, utcnow
from app.core.logging import log
from app.core.queue import MAX_ATTEMPTS, TaskContext, enqueue, task
from app.core.storage import get_storage
from app.models.knowledge import Document, KnowledgeSource
from app.rag import parsers
from app.rag.ingestion import ingest_document
from app.rag.parsers import ParsedDoc, ParseError

logger = log("rag")

URL_FETCH_TIMEOUT_SECONDS = 10.0
URL_FETCH_MAX_BYTES = 2 * 1024 * 1024  # 2MB


class FetchError(Exception):
    """A URL could not be fetched or is not indexable HTML."""


async def fetch_url(url: str, *, client: httpx.AsyncClient | None = None) -> ParsedDoc:
    """Download and parse one HTML page, enforcing timeout/size/type limits."""
    if not url.startswith(("http://", "https://")):
        raise FetchError(f"Unsupported URL scheme: {url}")
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=URL_FETCH_TIMEOUT_SECONDS, follow_redirects=True)
    try:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise FetchError(f"HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type != "text/html":
                raise FetchError(f"Not HTML (content-type: {content_type or 'unknown'})")
            body = b""
            async for part in response.aiter_bytes():
                body += part
                if len(body) > URL_FETCH_MAX_BYTES:
                    raise FetchError("Page exceeds the 2MB limit")
    except httpx.HTTPError as exc:
        raise FetchError(f"Fetch failed: {exc.__class__.__name__}: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()
    try:
        return parsers.extract(url, body, "text/html")
    except ParseError as exc:
        raise FetchError(str(exc)) from exc


async def load_document_text(document: Document) -> ParsedDoc:
    """Recover a document's extracted text from its uri (storage key or URL)."""
    uri = document.uri or ""
    if uri.startswith(("http://", "https://")):
        return await fetch_url(uri)
    if not uri:
        raise ParseError("Document has no stored content to ingest")
    data = await get_storage().read(uri)
    filename = document.meta.get("filename") or uri.rsplit("/", 1)[-1]
    return parsers.extract(filename, data, document.mime)


# Serializes RAG task bodies within one process: parallel ingest gains nothing
# (embedding is the bottleneck) and the in-process/SQLite setup shares a single
# DB connection where interleaved task transactions would collide. Locks are
# per event loop so test loops never cross.
_task_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _task_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _task_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _task_locks[loop] = lock
    return lock


@task("ingest_document")
async def ingest_document_task(ctx: TaskContext, *, document_id: str) -> None:
    async with _task_lock(), session_scope() as session:
        document = await session.get(Document, document_id)
        if document is None:
            # The enqueuing request may not have committed yet — retry via the
            # queue's backoff; a genuinely deleted document just logs and stops.
            if ctx.attempt < MAX_ATTEMPTS:
                raise RuntimeError(f"document {document_id} not visible yet")
            return
        document.status = "processing"
        document.error = None
        await session.flush()
        try:
            parsed = await load_document_text(document)
            await ingest_document(session, document, parsed.text)
        except Exception as exc:
            logger.warning("ingest failed for document %s: %s", document_id, exc)
            await session.rollback()
            document = await session.get(Document, document_id)
            if document is not None:
                document.status = "failed"
                document.error = str(exc)[:1000]


@task("sync_source")
async def sync_source_task(ctx: TaskContext, *, source_id: str) -> None:
    async with _task_lock(), session_scope() as session:
        source = await session.get(KnowledgeSource, source_id)
        if source is None:
            if ctx.attempt < MAX_ATTEMPTS:
                raise RuntimeError(f"source {source_id} not visible yet")
            return
        source.status = "syncing"
        source.error = None
        await session.flush()
        try:
            if source.type == "urls":
                errors = await _sync_urls(session, source)
            else:
                errors = await _requeue_stale_documents(session, source)
        except Exception as exc:  # infrastructure failure — record, don't crash
            logger.exception("sync failed for source %s", source_id)
            await session.rollback()
            source = await session.get(KnowledgeSource, source_id)
            if source is not None:
                source.status = "error"
                source.error = str(exc)[:1000]
                source.last_synced_at = utcnow()
            return
        source.last_synced_at = utcnow()
        if errors:
            source.status = "error"
            source.error = "; ".join(errors)[:1000]
        else:
            source.status = "idle"


async def _sync_urls(session: AsyncSession, source: KnowledgeSource) -> list[str]:
    urls = [u for u in (source.config or {}).get("urls", []) if isinstance(u, str) and u.strip()]
    errors: list[str] = []
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        for url in urls:
            url = url.strip()
            document = (
                await session.execute(
                    select(Document).where(Document.source_id == source.id, Document.uri == url)
                )
            ).scalar_one_or_none()
            try:
                parsed = await fetch_url(url, client=client)
            except FetchError as exc:
                errors.append(f"{url}: {exc}")
                if document is not None:
                    document.status = "failed"
                    document.error = str(exc)[:1000]
                continue
            if document is None:
                document = Document(
                    workspace_id=source.workspace_id,
                    source_id=source.id,
                    title=parsed.title,
                    uri=url,
                    mime="text/html",
                    status="processing",
                )
                session.add(document)
                await session.flush()
            else:
                document.title = parsed.title
                document.status = "processing"
                document.error = None
            try:
                await ingest_document(session, document, parsed.text)
            except Exception as exc:  # embedding/db trouble for one page
                logger.warning("ingest failed for url %s: %s", url, exc)
                errors.append(f"{url}: {exc}")
                document.status = "failed"
                document.error = str(exc)[:1000]
    return errors


async def _requeue_stale_documents(session: AsyncSession, source: KnowledgeSource) -> list[str]:
    """files/text/articles sources: re-enqueue anything that never indexed."""
    stale = (
        (
            await session.execute(
                select(Document.id).where(
                    Document.source_id == source.id, Document.status.in_(["pending", "failed"])
                )
            )
        )
        .scalars()
        .all()
    )
    for document_id in stale:
        await enqueue("ingest_document", document_id=document_id)
    return []
