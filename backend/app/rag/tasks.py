"""Background tasks for the RAG pipeline.

- ingest_document {document_id}: (re)load the raw content, extract, ingest.
- sync_source {source_id}: "urls" sources fetch each configured URL (10s
  timeout, 2MB cap, html/xhtml/plain/pdf) and ingest inline; connector sources
  (sitemap/crawl/github/notion/confluence/gdrive/zendesk) fetch their listing
  via `app.rag.connectors`, upsert by (source_id, uri), and prune documents
  that vanished from a *complete* listing (Onyx-style deletion pruning) —
  except zendesk, whose incremental listing only names changed articles, so
  only the ones it reports as archived are pruned; other source types
  re-ingest their non-indexed documents. Sitemap syncs are incremental: a URL
  whose <lastmod> matches the stored one is not refetched (it still counts as
  present, so pruning leaves it alone).

  Per-page problems never poison the source: a sync that fetched at least one
  document ends "idle" with the error/skip summary stored on the source
  (partial success). "error" is reserved for syncs where nothing at all could
  be fetched, or where the listing itself failed.

- knowledge_refresh_scan (scheduled, 60s): enqueues sync_source for every
  source whose config.refresh_minutes window has lapsed, and fails over
  sources stuck in "syncing" longer than the stuck-sync timeout (a crashed
  worker must not park a source forever).

All crawler HTTP goes through `crawl_client()` / `fetch_page()`: identifying
User-Agent, optional egress proxy, manual redirect following with the SSRF
guard re-run on every hop, and transient-failure retries (5xx/timeouts with
backoff, 429 honoring Retry-After capped at 30s).

Importing this module registers the tasks (`app.services.knowledge` imports it,
which the API package pulls in at startup).
"""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import session_scope, utcnow
from app.core.logging import log
from app.core.net import UnsafeUrlError, assert_public_url
from app.core.queue import MAX_ATTEMPTS, TaskContext, enqueue, task
from app.core.scheduler import scheduled
from app.core.storage import get_storage
from app.models.knowledge import Chunk, Document, KnowledgeSource
from app.rag import parsers
from app.rag.ingestion import ingest_document
from app.rag.parsers import ParsedDoc, ParseError

if TYPE_CHECKING:
    from app.rag.connectors import FetchedDoc, SitemapEntry

logger = log("rag")

# Source types synced through app.rag.connectors.
CONNECTOR_SOURCE_TYPES = frozenset(
    {"sitemap", "crawl", "github", "notion", "confluence", "gdrive", "zendesk"}
)

# The minimum allowed scheduled re-sync interval (config.refresh_minutes).
MIN_REFRESH_MINUTES = 5

URL_FETCH_TIMEOUT_SECONDS = 10.0
URL_FETCH_MAX_BYTES = 2 * 1024 * 1024  # 2MB

# Manual redirect following: every hop target re-runs the SSRF guard, because a
# public host is free to 302 into private address space after the first check.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECT_HOPS = 5

# Transient-failure retries for crawler fetches (5xx / timeouts / transport).
FETCH_MAX_RETRIES = 2  # extra attempts after the first failure
FETCH_RETRY_BASE_SECONDS = 0.5

RATE_LIMIT_MAX_RETRIES = 2  # extra attempts after a 429 before giving up
RATE_LIMIT_MAX_SLEEP_SECONDS = 30.0  # Retry-After is honored, but never longer than this
RATE_LIMIT_DEFAULT_SLEEP_SECONDS = 1.0  # missing/unparseable Retry-After

# Content types the page fetcher indexes (normalized to the parser mime).
# Anything else is a recorded skip — never a sync error.
PAGE_CONTENT_TYPES = {
    "text/html": "text/html",
    "application/xhtml+xml": "text/html",
    "text/plain": "text/plain",
    "application/pdf": "application/pdf",
}

# A source stuck in "syncing" this long is presumed crashed and failed over so
# scheduled refresh can resume (workers die; their transactions roll back, but
# the "syncing" flip committed by trigger_sync / scan_due_sources survives).
STUCK_SYNC_TIMEOUT = timedelta(minutes=30)


class FetchError(Exception):
    """A URL could not be fetched or its content could not be used."""


class SkippedContent(Exception):
    """The fetch worked, but the content type is one we deliberately don't
    index (images, archives, video, …). Callers record it as a skip note —
    never as a sync error."""


@dataclass
class FetchedPage:
    """One successfully downloaded page."""

    url: str  # final URL after redirect hops
    body: bytes
    content_type: str  # normalized: text/html | text/plain | application/pdf


def check_public_url(url: str) -> None:
    """SSRF guard for crawler fetch targets (urls/sitemap/crawl syncs).

    Thin adapter over :func:`app.core.net.assert_public_url` — the one egress
    policy shared with outbound webhooks and custom agent actions — re-raised as
    the :class:`FetchError` every sync path already handles. Run against the
    request URL *and every redirect target* before a request is issued.
    """
    try:
        assert_public_url(url)
    except UnsafeUrlError as exc:
        raise FetchError(str(exc)) from exc


def crawl_client(**kwargs: object) -> httpx.AsyncClient:
    """The one client shape for crawler fetches (crawl/sitemap/urls sources).

    - Sends the identifying `settings.crawl_user_agent` (WAFs block the httpx
      default, and site owners deserve a way to contact/allowlist us).
    - Never auto-follows redirects: `fetch_page` / `get_with_retry` hop
      manually so every redirect target passes the SSRF guard first.
    - Routes through `settings.crawl_proxy_url` when set. NOTE: with a proxy,
      DNS is resolved by the proxy, so `assert_public_url`'s local resolution
      is advisory only — literal-IP and loopback rejections still hold, but
      hostname checks can diverge from what the proxy resolves. Point the
      proxy at an egress that cannot reach internal networks.
    """
    settings = get_settings()
    headers = {"user-agent": settings.crawl_user_agent}
    extra_headers = kwargs.pop("headers", None)
    if isinstance(extra_headers, dict):
        headers.update(extra_headers)
    if settings.crawl_proxy_url:
        kwargs.setdefault("proxy", settings.crawl_proxy_url)
    return httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT_SECONDS,
        follow_redirects=False,
        headers=headers,
        **kwargs,  # type: ignore[arg-type]
    )


def retry_after_seconds(response: httpx.Response) -> float:
    """Sleep budget for a 429: the Retry-After header, capped — a provider
    asking for a 15-minute pause must not park the sync worker that long."""
    raw = str(response.headers.get("retry-after") or "").strip()
    try:
        seconds = float(raw)
    except ValueError:
        seconds = RATE_LIMIT_DEFAULT_SLEEP_SECONDS
    return max(0.0, min(seconds, RATE_LIMIT_MAX_SLEEP_SECONDS))


def _redirect_target(url: str, response: httpx.Response) -> str:
    location = str(response.headers.get("location") or "").strip()
    if not location:
        raise FetchError(f"HTTP {response.status_code} without a Location header")
    return urljoin(url, location)


async def _get_with_retries(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """One GET (no redirect following) that retries transient failures:
    timeouts/transport errors and 5xx with backoff, 429 honoring Retry-After
    (capped). Non-transient statuses are returned for the caller to judge."""
    attempts = FETCH_MAX_RETRIES + 1
    failure = "request failed"
    for attempt in range(attempts):
        delay = FETCH_RETRY_BASE_SECONDS * (2**attempt)
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            failure = f"Fetch failed: {exc.__class__.__name__}: {exc}"
        else:
            if response.status_code == 429:
                failure = "HTTP 429"
                delay = retry_after_seconds(response)
            elif response.status_code >= 500:
                failure = f"HTTP {response.status_code}"
            else:
                return response
        if attempt + 1 < attempts:
            await asyncio.sleep(delay)
    raise FetchError(failure)


async def get_with_retry(client: httpx.AsyncClient, url: str) -> tuple[httpx.Response, str]:
    """GET with manual redirect hops, the SSRF guard re-run per hop, and
    transient-failure retries. Returns (final non-redirect response, final
    URL); the caller judges the final status. For body-capped page fetches use
    :func:`fetch_page` instead."""
    current = url.strip()
    for _ in range(MAX_REDIRECT_HOPS + 1):
        check_public_url(current)
        response = await _get_with_retries(client, current)
        if response.status_code in REDIRECT_STATUSES:
            current = _redirect_target(current, response)
            continue
        return response, current
    raise FetchError(f"Stopped after {MAX_REDIRECT_HOPS} redirects")


def _resolve_page_type(url: str, content_type: str) -> str | None:
    resolved = PAGE_CONTENT_TYPES.get(content_type)
    if resolved is None and content_type in ("application/octet-stream", ""):
        # PDFs are frequently served as octet-stream; trust the extension.
        if urlsplit(url).path.lower().endswith(".pdf"):
            return "application/pdf"
    return resolved


async def _read_page_body(url: str, response: httpx.Response) -> FetchedPage:
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    resolved = _resolve_page_type(url, content_type)
    if resolved is None:
        raise SkippedContent(f"skipped (content-type {content_type or 'unknown'})")
    body = b""
    async for part in response.aiter_bytes():
        body += part
        if len(body) > URL_FETCH_MAX_BYTES:
            raise FetchError("Page exceeds the 2MB limit")
    return FetchedPage(url=url, body=body, content_type=resolved)


async def _stream_with_retries(client: httpx.AsyncClient, url: str) -> str | FetchedPage:
    """One streamed page request with transient-failure retries. Returns the
    redirect target (str) or the downloaded page. Raises FetchError for
    terminal failures and SkippedContent for non-indexable content types."""
    attempts = FETCH_MAX_RETRIES + 1
    failure = "request failed"
    for attempt in range(attempts):
        delay = FETCH_RETRY_BASE_SECONDS * (2**attempt)
        try:
            async with client.stream("GET", url) as response:
                status = response.status_code
                if status in REDIRECT_STATUSES:
                    return _redirect_target(url, response)
                if status == 200:
                    return await _read_page_body(url, response)
                if status == 429:
                    failure = "HTTP 429"
                    delay = retry_after_seconds(response)
                elif status >= 500:
                    failure = f"HTTP {status}"
                else:
                    raise FetchError(f"HTTP {status}")
        except (FetchError, SkippedContent):
            raise
        except httpx.HTTPError as exc:
            failure = f"Fetch failed: {exc.__class__.__name__}: {exc}"
        if attempt + 1 < attempts:
            await asyncio.sleep(delay)
    raise FetchError(failure)


async def fetch_page(url: str, *, client: httpx.AsyncClient | None = None) -> FetchedPage:
    """Download one page under the crawler's full policy.

    - http(s) only; the SSRF guard runs on the request URL and on every
      redirect target (max 5 hops) *before* the request is issued.
    - Transient failures retried (5xx/timeouts with backoff, 429 per
      Retry-After capped at 30s); 2MB streamed size cap.
    - Content types: text/html + application/xhtml+xml (→ html), text/plain,
      application/pdf (also .pdf URLs served as octet-stream). Anything else
      raises :class:`SkippedContent` for the caller to record as a skip.

    The returned page carries the *final* URL — for crawls it becomes the
    document URI and joins the visited/dedupe sets.
    """
    if not url.startswith(("http://", "https://")):
        raise FetchError(f"Unsupported URL scheme: {url}")
    owns_client = client is None
    if client is None:
        client = crawl_client()
    try:
        current = url.strip()
        for _ in range(MAX_REDIRECT_HOPS + 1):
            check_public_url(current)
            outcome = await _stream_with_retries(client, current)
            if isinstance(outcome, str):
                current = outcome
                continue
            return outcome
        raise FetchError(f"Stopped after {MAX_REDIRECT_HOPS} redirects")
    finally:
        if owns_client:
            await client.aclose()


async def fetch_url(url: str, *, client: httpx.AsyncClient | None = None) -> ParsedDoc:
    """Download and parse one page, enforcing the shared fetch policy."""
    page = await fetch_page(url, client=client)
    try:
        return parsers.extract(page.url, page.body, page.content_type)
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
            # queue's backoff. On the last attempt we cannot tell "deleted" from
            # "still not committed", so say so instead of returning silently: a
            # document that really did exist would otherwise sit at `pending`
            # forever with nothing in the log to explain why it has no chunks.
            if ctx.attempt < MAX_ATTEMPTS:
                raise RuntimeError(f"document {document_id} not visible yet")
            logger.warning(
                "giving up on document %s — never became visible (deleted, or its "
                "transaction never committed); re-sync the source to index it",
                document_id,
            )
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


@dataclass
class SyncReport:
    """What one sync accomplished.

    `errors` are hard failures (fetch/parse/ingest); `notes` are informational
    skips (unsupported content types, oversized files) that never flip the
    source red; `synced` counts documents fetched or confirmed unchanged — the
    partial-success gate: a sync with errors but `synced > 0` still ends
    "idle" (with the summary stored), because one broken page must not paint
    the whole source as failed.
    """

    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    synced: int = 0


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
        # Start stamp for stuck-sync detection (best effort: it only commits
        # with the sync itself; a crashed worker leaves the pre-sync flip's
        # updated_at as the fallback signal). Reassigned, not mutated — JSON
        # columns don't track in-place changes.
        source.config = {**(source.config or {}), "sync_started_at": utcnow().isoformat()}
        await session.flush()
        try:
            if source.type == "urls":
                report = await _sync_urls(session, source)
            elif source.type in CONNECTOR_SOURCE_TYPES:
                report = await _sync_connector(session, source)
            else:
                report = await _requeue_stale_documents(session, source)
        except Exception as exc:  # listing/infrastructure failure — record, don't crash
            logger.exception("sync failed for source %s", source_id)
            await session.rollback()
            source = await session.get(KnowledgeSource, source_id)
            if source is not None:
                source.status = "error"
                source.error = str(exc)[:1000]
                source.last_synced_at = utcnow()
            return
        source.last_synced_at = utcnow()
        problems = report.errors + report.notes
        source.error = "; ".join(problems)[:1000] if problems else None
        # Partial success stays "idle" (with the summary stored); "error" is
        # reserved for syncs where nothing at all could be fetched.
        source.status = "error" if report.errors and report.synced == 0 else "idle"


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
        if item.meta:  # reassigned, not mutated — JSON columns don't track in place
            document.meta = {**document.meta, **item.meta}
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
) -> tuple[list[FetchedDoc], list[str], list[str], set[str]]:
    """Fetch each URL under the shared crawler policy (SSRF-guarded redirects,
    retries, size cap, content-type routing).

    Returns (fetched, errors, skip notes, failed-but-listed uris). A fetch
    failure is recorded per URL and marks any existing Document failed — the
    URI still counts as *present* so listing-based pruning keeps it. An
    unsupported content type is a skip note, never an error. The document URI
    stays the requested URL (it is the identity key for urls/sitemap sources),
    even when the fetch was satisfied via redirects.
    """
    from app.rag.connectors import FetchedDoc

    fetched: list[FetchedDoc] = []
    errors: list[str] = []
    notes: list[str] = []
    failed: set[str] = set()
    seen: set[str] = set()
    async with crawl_client() as client:
        for url in urls:
            url = url.strip()
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                page = await fetch_page(url, client=client)
                parsed = parsers.extract(page.url, page.body, page.content_type)
            except SkippedContent as exc:
                notes.append(f"{url}: {exc}")
                continue
            except (FetchError, ParseError) as exc:
                errors.append(f"{url}: {exc}")
                failed.add(url)
                document = await _get_document_by_uri(session, source, url)
                if document is not None:
                    document.status = "failed"
                    document.error = str(exc)[:1000]
                continue
            fetched.append(
                FetchedDoc(title=parsed.title, text=parsed.text, uri=url, mime=page.content_type)
            )
    return fetched, errors, notes, failed


async def _sync_urls(session: AsyncSession, source: KnowledgeSource) -> SyncReport:
    urls = [u for u in (source.config or {}).get("urls", []) if isinstance(u, str) and u.strip()]
    fetched, errors, notes, _failed = await _fetch_url_list(session, source, urls)
    errors += await upsert_and_ingest(session, source, fetched)
    return SyncReport(errors=errors, notes=notes, synced=len(fetched))


async def _sync_sitemap(
    session: AsyncSession, source: KnowledgeSource, entries: list[SitemapEntry]
) -> tuple[list[FetchedDoc], list[str], list[str], set[str], set[str]]:
    """Fetch the sitemap's pages, skipping URLs whose <lastmod> still matches
    the stored one. Returns (fetched, errors, notes, failed-uris, unchanged) —
    unchanged and failed URIs are still "present" for pruning purposes."""
    unchanged: set[str] = set()
    stale: list[SitemapEntry] = []
    for entry in entries:
        document = await _get_document_by_uri(session, source, entry.url)
        if (
            entry.lastmod  # no stamp → always refetch
            and document is not None
            and document.status == "indexed"
            and document.meta.get("lastmod") == entry.lastmod
        ):
            unchanged.add(entry.url)
        else:
            stale.append(entry)
    fetched, errors, notes, failed = await _fetch_url_list(
        session, source, [entry.url for entry in stale]
    )
    lastmods = {entry.url: entry.lastmod for entry in stale if entry.lastmod}
    for item in fetched:
        lastmod = lastmods.get(item.uri)
        if lastmod:
            item.meta["lastmod"] = lastmod
    return fetched, errors, notes, failed, unchanged


async def _sync_connector(session: AsyncSession, source: KnowledgeSource) -> SyncReport:
    """Connector sources: fetch the listing, upsert + ingest, then prune.

    Full-listing types (sitemap/crawl/github/notion/confluence/gdrive) prune
    documents that disappeared — but only when the listing completed (for
    crawls: frontier exhausted or max_pages hit, never a wall-clock stop) and
    the sync fetched something (or had no errors at all): a transient total
    outage must never wipe a source. URIs whose *fetch* failed this round are
    still part of the listing, so their documents are kept, marked failed.
    zendesk is incremental: unchanged articles are absent from its listing, so
    only the articles it names as archived are pruned, and the sync_cursor the
    fetch wrote into the config copy is persisted. Skip notes (unsupported
    content types, oversized files) are recorded in the source summary but
    never block pruning and never fail the sync.
    """
    from app.rag import connectors
    from app.services.knowledge import get_source_secrets

    config = dict(source.config or {})
    secrets = get_source_secrets(source)
    fetch_errors: list[str] = []
    notes: list[str] = []
    present: set[str] = set()
    unchanged: set[str] = set()
    archived: list[str] = []
    full_listing = True
    listing_complete = True
    if source.type == "sitemap":
        entries = await connectors.fetch_sitemap(config)
        fetched, fetch_errors, notes, failed, unchanged = await _sync_sitemap(
            session, source, entries
        )
        present = failed
    elif source.type == "crawl":
        result = await connectors.crawl_site(config)
        fetched = result.docs
        fetch_errors = result.errors
        notes = result.notes
        present = set(result.failed_uris)
        listing_complete = result.listing_complete
        for uri, message in result.failed_uris.items():
            document = await _get_document_by_uri(session, source, uri)
            if document is not None:  # kept from pruning, but visibly failed
                document.status = "failed"
                document.error = message[:1000]
    elif source.type == "github":
        fetched = await connectors.fetch_github(config, secrets)
    elif source.type == "notion":
        fetched = await connectors.fetch_notion(
            config, secrets, session=session, workspace_id=source.workspace_id
        )
    elif source.type == "confluence":
        fetched = await connectors.fetch_confluence(
            config, secrets, session=session, workspace_id=source.workspace_id
        )
    elif source.type == "gdrive":
        fetched, notes = await connectors.fetch_gdrive(
            config, secrets, session=session, workspace_id=source.workspace_id
        )
    else:  # zendesk — incremental listing + cursor writeback
        fetched, archived = await connectors.fetch_zendesk(
            config, secrets, session=session, workspace_id=source.workspace_id
        )
        full_listing = False
        if config != (source.config or {}):
            source.config = config  # reassigned, not mutated — persists sync_cursor
    errors = fetch_errors + await upsert_and_ingest(session, source, fetched)
    succeeded = bool(fetched or unchanged) or not errors
    if full_listing and listing_complete and succeeded:
        keep_uris = {item.uri for item in fetched} | unchanged | present
        await _prune_missing_documents(session, source, keep_uris=keep_uris)
    if archived:
        await _prune_documents_by_uri(session, source, uris=set(archived))
    return SyncReport(errors=errors, notes=notes, synced=len(fetched) + len(unchanged))


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


async def _prune_documents_by_uri(
    session: AsyncSession, source: KnowledgeSource, *, uris: set[str]
) -> None:
    """Delete exactly these documents (and their chunks) — zendesk's archived
    articles, which an incremental listing names instead of omitting."""
    doomed = select(Document.id).where(
        Document.workspace_id == source.workspace_id,
        Document.source_id == source.id,
        Document.uri.in_(uris),
    )
    await session.execute(delete(Chunk).where(Chunk.document_id.in_(doomed)))
    await session.execute(
        delete(Document).where(
            Document.workspace_id == source.workspace_id,
            Document.source_id == source.id,
            Document.uri.in_(uris),
        )
    )


async def _requeue_stale_documents(session: AsyncSession, source: KnowledgeSource) -> SyncReport:
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
    return SyncReport()


# ---------------------------------------------------------------------------
# scheduled re-sync
# ---------------------------------------------------------------------------


def _sync_activity_at(source: KnowledgeSource) -> datetime | None:
    """When this source's sync state last moved: the recorded start stamp, or
    the row's own timestamps (the "syncing" flip bumps updated_at, and a
    crashed worker's rollback cannot un-bump it)."""
    candidates: list[datetime] = []
    raw = (source.config or {}).get("sync_started_at")
    if isinstance(raw, str):
        try:
            candidates.append(datetime.fromisoformat(raw))
        except ValueError:
            pass
    for stamp in (source.updated_at, source.last_synced_at):
        if stamp is not None:
            candidates.append(stamp)
    aware = [stamp for stamp in candidates if stamp.tzinfo is not None]
    return max(aware) if aware else None


async def scan_due_sources() -> int:
    """Enqueue sync_source for every source whose refresh window has lapsed.

    First, sources stuck in "syncing" longer than STUCK_SYNC_TIMEOUT are failed
    over ("sync timed out") — a crashed worker's rollback leaves the committed
    "syncing" flip behind, and without this reset the source would never
    refresh again. A source is then due when config.refresh_minutes is an int
    >= 5, it is not syncing, and last_synced_at is unset or older than the
    window. Due sources are flipped to "syncing" (mirroring trigger_sync) with
    a fresh start stamp so back-to-back scans never double-enqueue. Returns
    the number enqueued.
    """
    due: list[str] = []
    async with _task_lock(), session_scope() as session:
        now = utcnow()
        stuck_rows = await session.execute(
            select(KnowledgeSource).where(KnowledgeSource.status == "syncing")
        )
        for source in stuck_rows.scalars():
            moved_at = _sync_activity_at(source)
            if moved_at is not None and now - moved_at > STUCK_SYNC_TIMEOUT:
                logger.warning(
                    "source %s stuck in syncing since %s — failing over",
                    source.id,
                    moved_at.isoformat(),
                )
                source.status = "error"
                source.error = "sync timed out"
        rows = await session.execute(
            select(KnowledgeSource).where(KnowledgeSource.status != "syncing")
        )
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
            source.config = {**(source.config or {}), "sync_started_at": now.isoformat()}
            due.append(source.id)
    for source_id in due:
        await enqueue("sync_source", source_id=source_id)
    return len(due)


@scheduled("knowledge_refresh_scan", every_seconds=60)
async def _knowledge_refresh_scan() -> None:
    """Scheduler tick: enqueue re-syncs for sources with refresh_minutes set."""
    await scan_due_sources()
