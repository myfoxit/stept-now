"""Knowledge sources, documents, and hybrid search (service layer)."""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import (
    AppError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    ValidationFailure,
)
from app.core.events import Actor
from app.core.queue import enqueue
from app.core.security import decrypt_secret, encrypt_secret
from app.core.storage import get_storage
from app.models.knowledge import Chunk, Document, KnowledgeSource
from app.rag import tasks as rag_tasks  # noqa: F401  (registers queue tasks on import)
from app.rag.ingestion import ingest_document
from app.rag.parsers import ParseError, extract, normalize_mime
from app.rag.retrieval import search_chunks
from app.schemas.knowledge import RetrievedChunkOut, SearchResponse, SourceOut
from app.services import audit
from app.services.search_analytics import record_search

ARTICLES_SOURCE_NAME = "Help center articles"

# Source types that support scheduled re-sync via config.refresh_minutes.
REFRESHABLE_TYPES = frozenset({"urls", "sitemap", "crawl", "github", "notion"})

# Files in one batch upload request.
MAX_BATCH_FILES = 20

# Crawl include_patterns/exclude_patterns limits.
MAX_CRAWL_PATTERNS = 20

# Authored documents (uploaded/pasted plain text) can be edited in place.
EDITABLE_SOURCE_TYPES = frozenset({"files", "text"})
EDITABLE_MIMES = frozenset({"text/markdown", "text/plain"})
# Raw content returned on the document detail endpoint (TipTap editing).
DOCUMENT_CONTENT_MAX_BYTES = 200 * 1024


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


def set_source_secrets(source: KnowledgeSource, secrets: dict[str, Any] | None) -> None:
    """Store connector credentials Fernet-encrypted; empty/None clears them."""
    source.secrets_encrypted = encrypt_secret(json.dumps(secrets)) if secrets else None


def get_source_secrets(source: KnowledgeSource) -> dict[str, Any]:
    if not source.secrets_encrypted:
        return {}
    decrypted = json.loads(decrypt_secret(source.secrets_encrypted))
    return decrypted if isinstance(decrypted, dict) else {}


def _require_http_url(config: dict[str, Any], key: str, source_type: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip().startswith(("http://", "https://")):
        raise ValidationFailure(f"{source_type} sources need config.{key}: an http(s) URL")
    return value.strip()


def _clamp_int(config: dict[str, Any], key: str, *, cap: int, minimum: int = 1) -> None:
    value = config.get(key)
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationFailure(f"config.{key} must be an integer >= {minimum}")
    config[key] = min(value, cap)


def _clean_patterns(config: dict[str, Any], key: str) -> None:
    """Normalize a crawl glob list: ≤20 entries, each a ≤200 char string."""
    value = config.get(key)
    if value is None:
        return
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationFailure(f"config.{key} must be a list of glob strings")
    patterns = [item.strip() for item in value if item.strip()]
    if any(len(pattern) > 200 for pattern in patterns):
        raise ValidationFailure(f"config.{key} entries are limited to 200 characters")
    if len(patterns) > MAX_CRAWL_PATTERNS:
        raise ValidationFailure(f"config.{key} is limited to {MAX_CRAWL_PATTERNS} patterns")
    config[key] = patterns


def _validate_config(source_type: str, config: dict[str, Any]) -> dict[str, Any]:
    config = dict(config)
    if source_type == "urls":
        urls = config.get("urls")
        if not isinstance(urls, list) or not urls:
            raise ValidationFailure("urls sources need config.urls: a non-empty list of URLs")
        for url in urls:
            if not isinstance(url, str) or not url.strip().startswith(("http://", "https://")):
                raise ValidationFailure(f"Invalid URL in config.urls: {url!r}")
        config["urls"] = [url.strip() for url in urls]
    elif source_type == "sitemap":
        config["sitemap_url"] = _require_http_url(config, "sitemap_url", source_type)
        _clamp_int(config, "max_pages", cap=500)
    elif source_type == "crawl":
        config["base_url"] = _require_http_url(config, "base_url", source_type)
        _clamp_int(config, "max_pages", cap=200)
        _clamp_int(config, "max_depth", cap=5, minimum=0)
        _clamp_int(config, "delay_ms", cap=2000, minimum=0)
        _clean_patterns(config, "include_patterns")
        _clean_patterns(config, "exclude_patterns")
        if "respect_robots" in config and not isinstance(config["respect_robots"], bool):
            raise ValidationFailure("config.respect_robots must be a boolean")
    elif source_type == "github":
        for key in ("repo_owner", "repo"):
            value = config.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValidationFailure(f"github sources need config.{key}")
            config[key] = value.strip()
        branch = config.get("branch")
        if branch is not None and (not isinstance(branch, str) or not branch.strip()):
            raise ValidationFailure("config.branch must be a non-empty string")
        for key in ("include_files", "include_issues", "include_prs"):
            if key in config and not isinstance(config[key], bool):
                raise ValidationFailure(f"config.{key} must be a boolean")
    elif source_type == "notion":
        root_page_id = config.get("root_page_id")
        if root_page_id is not None and (
            not isinstance(root_page_id, str) or not root_page_id.strip()
        ):
            raise ValidationFailure("config.root_page_id must be a non-empty string")
        _clamp_int(config, "max_pages", cap=300)
    if source_type in REFRESHABLE_TYPES:
        refresh = config.get("refresh_minutes")
        if refresh is not None and (
            isinstance(refresh, bool) or not isinstance(refresh, int) or refresh < 5
        ):
            raise ValidationFailure("config.refresh_minutes must be an integer >= 5")
    boost = config.get("boost")
    if boost is not None and not isinstance(boost, (int, float)):
        raise ValidationFailure("config.boost must be a number")
    return config


def _validate_secrets(source_type: str, secrets: dict[str, Any] | None, *, creating: bool) -> None:
    """notion requires an integration token; other types take secrets as-is."""
    if source_type != "notion":
        return
    if secrets is None and not creating:  # PATCH without secrets keeps the stored token
        return
    if not str((secrets or {}).get("token") or "").strip():
        raise ValidationFailure("notion sources need secrets.token (internal integration token)")


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
    out.has_secrets = source.secrets_encrypted is not None
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
    secrets: dict[str, Any] | None = None,
) -> KnowledgeSource:
    if type == "articles":
        raise BadRequestError("The articles source is managed automatically")
    _validate_secrets(type, secrets, creating=True)
    source = KnowledgeSource(
        workspace_id=workspace_id,
        type=type,
        name=name.strip(),
        config=_validate_config(type, config or {}),
    )
    set_source_secrets(source, secrets)
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
    secrets: dict[str, Any] | None = None,
) -> KnowledgeSource:
    source = await get_source(session, workspace_id, source_id)
    if name is not None:
        source.name = name.strip()
    if config is not None:
        source.config = _validate_config(source.type, config)
    if secrets is not None:  # {} clears, non-empty re-encrypts
        _validate_secrets(source.type, secrets, creating=False)
        set_source_secrets(source, secrets)
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


async def add_documents_from_files(
    session: AsyncSession,
    workspace_id: str,
    source: KnowledgeSource,
    *,
    actor: Actor,
    files: list[tuple[str, bytes, str | None]],
) -> list[Document]:
    """Batch upload: [(filename, data, content_type), …] → one Document each.

    Every file is validated/stored independently — an empty, oversized, or
    unparseable file becomes a `failed` Document carrying the reason instead of
    aborting the batch. Infrastructure failures still raise.
    """
    if source.type == "articles":
        raise BadRequestError("The articles source is managed automatically")
    if not files:
        raise BadRequestError("Batch uploads need at least one 'file' field")
    if len(files) > MAX_BATCH_FILES:
        raise BadRequestError(f"Batch uploads are limited to {MAX_BATCH_FILES} files")
    documents: list[Document] = []
    for filename, data, content_type in files:
        try:
            document = await add_document_from_file(
                session,
                workspace_id,
                source,
                actor=actor,
                filename=filename,
                data=data,
                content_type=content_type,
            )
        except AppError as exc:
            document = Document(
                workspace_id=workspace_id,
                source_id=source.id,
                title=filename[:400] or "Untitled",
                mime=normalize_mime(filename, content_type),
                status="failed",
                error=exc.message[:1000],
                meta={"filename": filename, "size": len(data)},
            )
            session.add(document)
            await session.flush()
        documents.append(document)
    return documents


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


def is_editable_document(document: Document, source: KnowledgeSource) -> bool:
    """True for authored documents: storage-key-backed text/markdown living in
    a files/text source. URL-, portal- and connector-backed documents are
    rendered from somewhere else and cannot be edited here."""
    if source.type not in EDITABLE_SOURCE_TYPES:
        return False
    uri = document.uri or ""
    if not uri or uri.startswith(("http://", "https://", "/")):
        return False
    return (document.mime or "") in EDITABLE_MIMES


async def document_content(document: Document, source: KnowledgeSource) -> str | None:
    """Raw stored text of an editable document (the TipTap editing surface).

    None for anything non-editable, missing from storage, or bigger than
    DOCUMENT_CONTENT_MAX_BYTES.
    """
    if not is_editable_document(document, source):
        return None
    try:
        data = await get_storage().read(document.uri or "")
    except (NotFoundError, OSError):
        return None
    if len(data) > DOCUMENT_CONTENT_MAX_BYTES:
        return None
    return data.decode("utf-8", errors="replace")


async def update_document(
    session: AsyncSession,
    workspace_id: str,
    document_id: str,
    *,
    actor: Actor,
    title: str | None = None,
    content: str | None = None,
    ai_searchable: bool | None = None,
) -> Document:
    """Re-edit an authored document: store the new text and re-index inline.

    Re-ingestion is synchronous so the caller sees the indexed result straight
    away; unchanged content short-circuits inside `ingest_document` (chunk ids
    stay stable). A changed title forces a re-chunk because the title is
    prefixed onto every chunk. Non-editable documents raise ConflictError.

    ``ai_searchable`` toggles the retrieval opt-out and works on ANY document
    (connector/portal-backed included) — it filters at query time, so flipping
    it never re-chunks or re-embeds anything.
    """
    document = await get_document(session, workspace_id, document_id)
    source = await get_source(session, workspace_id, document.source_id)
    if ai_searchable is not None:
        document.ai_searchable = ai_searchable
    if title is None and content is None:
        # Flag-only (or empty) PATCH: no text changed, nothing to re-index.
        await session.flush()
        await audit.record(
            session,
            workspace_id,
            actor=actor,
            action="knowledge.document.update",
            target_type="document",
            target_id=document.id,
            meta={"title": document.title, "ai_searchable": document.ai_searchable},
        )
        return document
    if not is_editable_document(document, source):
        raise ConflictError("Only authored text documents can be edited")
    if title is not None:
        new_title = title.strip()[:400]
        if not new_title:
            raise ValidationFailure("title must not be empty")
        if new_title != document.title:
            document.title = new_title
            document.content_hash = None  # title is chunked into the content
    if content is not None:
        old_key = document.uri or ""
        filename = _authored_filename(document)
        stored = await get_storage().save(filename, content.encode("utf-8"))
        document.uri = stored.key
        document.meta = {**document.meta, "filename": filename, "size": stored.size}
        if old_key and old_key != stored.key:
            await get_storage().delete(old_key)
    document.status = "processing"
    document.error = None
    await session.flush()
    parsed = await rag_tasks.load_document_text(document)
    await ingest_document(session, document, parsed.text)
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="knowledge.document.update",
        target_type="document",
        target_id=document.id,
        meta={"title": document.title},
    )
    return document


def _authored_filename(document: Document) -> str:
    """Storage filename for a re-saved authored doc: current title + the
    original extension (markdown by default)."""
    previous = str(document.meta.get("filename") or "")
    extension = f".{previous.rsplit('.', 1)[1]}" if "." in previous else ".md"
    return f"{document.title.strip() or 'document'}{extension}"


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
    rerank: bool = False,
) -> SearchResponse:
    started = time.perf_counter()
    results = await search_chunks(
        session, workspace_id, query, k=k, source_ids=source_ids, rerank=rerank
    )
    latency_ms = (time.perf_counter() - started) * 1000
    await record_search(
        session,
        workspace_id,
        query=query,
        source="playground",
        results_count=len(results),
        top_score=results[0].score if results else None,
        latency_ms=int(latency_ms),
    )
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
