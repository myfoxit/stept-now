"""Background tasks for the RAG pipeline.

- ingest_document {document_id}: (re)load the raw content, extract, ingest.
- sync_source {source_id}: "urls" sources fetch each configured URL (10s
  timeout, 2MB cap, text/html only) and ingest inline; connector sources
  (sitemap/crawl/github/notion) fetch their full listing via
  `app.rag.connectors`, upsert by (source_id, uri), and prune documents that
  vanished from a complete, error-free listing (Onyx-style deletion pruning);
  other source types re-ingest their non-indexed documents. Failures mark the
  document/source as errored — the task itself never raises for content
  problems.
- knowledge_refresh_scan (scheduled, 60s): enqueues sync_source for every
  source whose config.refresh_minutes window has lapsed.

Importing this module registers the tasks (`app.services.knowledge` imports it,
which the API package pulls in at startup).
"""

from __future__ import annotations

import asyncio
import weakref
from datetime import timedelta
from typing import TYPE_CHECKING

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope, utcnow
from app.core.logging import log
from app.core.queue import MAX_ATTEMPTS, TaskContext, enqueue, task
from app.core.scheduler import scheduled
from app.core.storage import get_storage
from app.models.knowledge import Chunk, Document, KnowledgeSource
from app.rag import parsers
from app.rag.ingestion import ingest_document
from app.rag.parsers import ParsedDoc, ParseError

if TYPE_CHECKING:
    from app.rag.connectors import FetchedDoc

logger = log("rag")

# Source types synced through app.rag.connectors (full-listing + pruning).
CONNECTOR_SOURCE_TYPES = frozenset({"sitemap", "crawl", "github", "notion"})

# The minimum allowed scheduled re-sync interval (config.refresh_minutes).
MIN_REFRESH_MINUTES = 5

URL_FETCH_TIMEOUT_SECONDS = 10.0
URL_FETCH_MAX_BYTES = 2 * 1024 * 1024  # 2MB


class FetchError(Exception):
    """A URL could not be fetched or is not indexable HTML."""


async def fetch_html_bytes(url: str, *, client: httpx.AsyncClient | None = None) -> bytes:
    """Download one HTML page's raw bytes, enforcing timeout/size/type limits."""
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
        return body
    except httpx.HTTPError as exc:
        raise FetchError(f"Fetch failed: {exc.__class__.__name__}: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()


async def fetch_url(url: str, *, client: httpx.AsyncClient | None = None) -> ParsedDoc:
    """Download and parse one HTML page, enforcing timeout/size/type limits."""
    body = await fetch_html_bytes(url, client=client)
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
            elif source.type in CONNECTOR_SOURCE_TYPES:
                errors = await _sync_connector(session, source)
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


async def _get_document_by_uri(
    session: AsyncSession, source: KnowledgeSource, uri: str
) -> Document | None:
    return (
        await session.execute(
            select(Document).where(Document.source_id == source.id, Document.uri == uri)
        )
    ).scalar_one_or_none()


async def upsert_and_ingest(
    session: AsyncSession, source: KnowledgeSource, fetched: list[FetchedDoc]
) -> list[str]:
    """Upsert Documents by (source_id, uri) and ingest each; returns per-doc
    error strings (one bad document never fails the whole sync)."""
    errors: list[str] = []
    for item in fetched:
        document = await _get_document_by_uri(session, source, item.uri)
        if document is None:
            document = Document(
                workspace_id=source.workspace_id,
                source_id=source.id,
                title=item.title[:400],
                uri=item.uri,
                mime=item.mime,
                status="processing",
            )
            session.add(document)
            await session.flush()
        else:
            document.title = item.title[:400]
            document.mime = item.mime
            document.status = "processing"
            document.error = None
        try:
            await ingest_document(session, document, item.text)
        except Exception as exc:  # embedding/db trouble for one document
            logger.warning("ingest failed for %s: %s", item.uri, exc)
            errors.append(f"{item.uri}: {exc}")
            document.status = "failed"
            document.error = str(exc)[:1000]
    return errors


async def _fetch_url_list(
    session: AsyncSession, source: KnowledgeSource, urls: list[str]
) -> tuple[list[FetchedDoc], list[str]]:
    """Fetch each URL as an HTML doc (SSRF-guarded, shared limits). Failures
    are recorded per URL and mark any existing Document failed — never fatal."""
    from app.rag.connectors import FetchedDoc, check_public_url

    fetched: list[FetchedDoc] = []
    errors: list[str] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        for url in urls:
            url = url.strip()
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                check_public_url(url)
                parsed = await fetch_url(url, client=client)
            except FetchError as exc:
                errors.append(f"{url}: {exc}")
                document = await _get_document_by_uri(session, source, url)
                if document is not None:
                    document.status = "failed"
                    document.error = str(exc)[:1000]
                continue
            fetched.append(FetchedDoc(title=parsed.title, text=parsed.text, uri=url))
    return fetched, errors


async def _sync_urls(session: AsyncSession, source: KnowledgeSource) -> list[str]:
    urls = [u for u in (source.config or {}).get("urls", []) if isinstance(u, str) and u.strip()]
    fetched, errors = await _fetch_url_list(session, source, urls)
    return errors + await upsert_and_ingest(session, source, fetched)


async def _sync_connector(session: AsyncSession, source: KnowledgeSource) -> list[str]:
    """sitemap/crawl/github/notion: fetch the full listing, upsert + ingest,
    then prune documents that disappeared — but only when the listing was
    complete and every document synced cleanly (never prune on partial failure)."""
    from app.rag import connectors
    from app.services.knowledge import get_source_secrets

    config = source.config or {}
    fetch_errors: list[str] = []
    if source.type == "sitemap":
        urls = await connectors.fetch_sitemap(config)
        fetched, fetch_errors = await _fetch_url_list(session, source, urls)
    elif source.type == "crawl":
        fetched, fetch_errors = await connectors.crawl_site(config)
    elif source.type == "github":
        fetched = await connectors.fetch_github(config, get_source_secrets(source))
    else:  # notion
        fetched = await connectors.fetch_notion(config, get_source_secrets(source))
    errors = fetch_errors + await upsert_and_ingest(session, source, fetched)
    if not errors:
        await _prune_missing_documents(session, source, keep_uris={item.uri for item in fetched})
    return errors


async def _prune_missing_documents(
    session: AsyncSession, source: KnowledgeSource, *, keep_uris: set[str]
) -> None:
    """Delete documents (and their chunks) that vanished from a complete
    connector listing — Onyx-style deletion pruning."""
    doomed = select(Document.id).where(
        Document.workspace_id == source.workspace_id,
        Document.source_id == source.id,
        Document.uri.not_in(keep_uris),
    )
    await session.execute(delete(Chunk).where(Chunk.document_id.in_(doomed)))
    await session.execute(
        delete(Document).where(
            Document.workspace_id == source.workspace_id,
            Document.source_id == source.id,
            Document.uri.not_in(keep_uris),
        )
    )


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


# ---------------------------------------------------------------------------
# scheduled re-sync
# ---------------------------------------------------------------------------


async def scan_due_sources() -> int:
    """Enqueue sync_source for every source whose refresh window has lapsed.

    A source is due when config.refresh_minutes is an int >= 5, it is not
    already syncing, and last_synced_at is unset or older than the window.
    Due sources are flipped to "syncing" (mirroring trigger_sync) so back-to-
    back scans never double-enqueue. Returns the number enqueued.
    """
    due: list[str] = []
    async with _task_lock(), session_scope() as session:
        rows = await session.execute(
            select(KnowledgeSource).where(KnowledgeSource.status != "syncing")
        )
        now = utcnow()
        for source in rows.scalars():
            refresh = (source.config or {}).get("refresh_minutes")
            if isinstance(refresh, bool) or not isinstance(refresh, int):
                continue
            if refresh < MIN_REFRESH_MINUTES:
                continue
            if source.last_synced_at is not None and now - source.last_synced_at < timedelta(
                minutes=refresh
            ):
                continue
            source.status = "syncing"
            due.append(source.id)
    for source_id in due:
        await enqueue("sync_source", source_id=source_id)
    return len(due)


@scheduled("knowledge_refresh_scan", every_seconds=60)
async def _knowledge_refresh_scan() -> None:
    """Scheduler tick: enqueue re-syncs for sources with refresh_minutes set."""
    await scan_due_sources()
