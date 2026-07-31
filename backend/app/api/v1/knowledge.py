"""Knowledge base API: sources, documents, ingestion, hybrid search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.core.deps import Db, Member, require_perm
from app.core.errors import BadRequestError, ValidationFailure
from app.core.events import Actor
from app.core.pagination import OffsetPage, clamp_limit
from app.core.permissions import Perm
from app.schemas.common import Msg
from app.schemas.knowledge import (
    ChunkPreviewOut,
    DocumentDetailOut,
    DocumentOut,
    SearchRequest,
    SearchResponse,
    SourceCreate,
    SourceOut,
    SourceUpdate,
    TextDocumentCreate,
)
from app.services import knowledge as knowledge_service

router = APIRouter()

KnowledgeRead = Depends(require_perm(Perm.KNOWLEDGE_READ))
KnowledgeWrite = Depends(require_perm(Perm.KNOWLEDGE_WRITE))


def _actor(principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


# --- sources ----------------------------------------------------------------


@router.get("/knowledge/sources", response_model=list[SourceOut], dependencies=[KnowledgeRead])
async def list_sources(principal: Member, session: Db):
    return await knowledge_service.list_sources(session, principal.workspace.id)


@router.post(
    "/knowledge/sources",
    response_model=SourceOut,
    status_code=201,
    dependencies=[KnowledgeWrite],
)
async def create_source(body: SourceCreate, principal: Member, session: Db):
    source = await knowledge_service.create_source(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        type=body.type,
        name=body.name,
        config=body.config,
    )
    return knowledge_service.source_out(source, 0)


@router.get(
    "/knowledge/sources/{source_id}", response_model=SourceOut, dependencies=[KnowledgeRead]
)
async def get_source(source_id: str, principal: Member, session: Db):
    source = await knowledge_service.get_source(session, principal.workspace.id, source_id)
    count = await knowledge_service.count_documents(session, source.id)
    return knowledge_service.source_out(source, count)


@router.patch(
    "/knowledge/sources/{source_id}", response_model=SourceOut, dependencies=[KnowledgeWrite]
)
async def update_source(source_id: str, body: SourceUpdate, principal: Member, session: Db):
    source = await knowledge_service.update_source(
        session,
        principal.workspace.id,
        source_id,
        actor=_actor(principal),
        name=body.name,
        config=body.config,
    )
    count = await knowledge_service.count_documents(session, source.id)
    return knowledge_service.source_out(source, count)


@router.delete("/knowledge/sources/{source_id}", response_model=Msg, dependencies=[KnowledgeWrite])
async def delete_source(source_id: str, principal: Member, session: Db):
    await knowledge_service.delete_source(
        session, principal.workspace.id, source_id, actor=_actor(principal)
    )
    return Msg(message="Source deleted")


@router.post(
    "/knowledge/sources/{source_id}/sync",
    response_model=SourceOut,
    dependencies=[KnowledgeWrite],
)
async def sync_source(source_id: str, principal: Member, session: Db):
    source = await knowledge_service.trigger_sync(
        session, principal.workspace.id, source_id, actor=_actor(principal)
    )
    count = await knowledge_service.count_documents(session, source.id)
    return knowledge_service.source_out(source, count)


@router.post(
    "/knowledge/sources/{source_id}/documents",
    response_model=DocumentOut,
    status_code=201,
    dependencies=[KnowledgeWrite],
)
async def add_document(source_id: str, request: Request, principal: Member, session: Db):
    """Add a document: multipart file upload (`file` field) or JSON
    `{title, content}` for pasted text. Parsing errors return 400."""
    source = await knowledge_service.get_source(session, principal.workspace.id, source_id)
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise BadRequestError("Multipart uploads need a 'file' field")
        data = await upload.read()
        document = await knowledge_service.add_document_from_file(
            session,
            principal.workspace.id,
            source,
            actor=_actor(principal),
            filename=upload.filename or "file",
            data=data,
            content_type=upload.content_type,
        )
    else:
        try:
            body = TextDocumentCreate.model_validate(await request.json())
        except ValidationError as exc:
            raise ValidationFailure(
                "Request validation failed", details={"errors": exc.errors(include_url=False)}
            ) from exc
        except ValueError as exc:
            raise BadRequestError("Body must be JSON {title, content} or multipart") from exc
        document = await knowledge_service.add_document_from_text(
            session,
            principal.workspace.id,
            source,
            actor=_actor(principal),
            title=body.title,
            content=body.content,
        )
    return DocumentOut.model_validate(document)


# --- documents --------------------------------------------------------------


@router.get(
    "/knowledge/documents", response_model=OffsetPage[DocumentOut], dependencies=[KnowledgeRead]
)
async def list_documents(
    principal: Member,
    session: Db,
    source_id: str | None = None,
    status: str | None = None,
    limit: int | None = None,
    offset: int = 0,
):
    limit = clamp_limit(limit)
    documents, total = await knowledge_service.list_documents(
        session,
        principal.workspace.id,
        source_id=source_id,
        status=status,
        limit=limit,
        offset=max(offset, 0),
    )
    return OffsetPage(
        items=[DocumentOut.model_validate(document) for document in documents],
        total=total,
        limit=limit,
        offset=max(offset, 0),
    )


@router.get(
    "/knowledge/documents/{document_id}",
    response_model=DocumentDetailOut,
    dependencies=[KnowledgeRead],
)
async def get_document(document_id: str, principal: Member, session: Db):
    document = await knowledge_service.get_document(session, principal.workspace.id, document_id)
    chunks = await knowledge_service.get_document_chunks(session, document.id, limit=5)
    detail = DocumentDetailOut.model_validate(document)
    detail.chunks = [ChunkPreviewOut.model_validate(chunk) for chunk in chunks]
    return detail


@router.delete(
    "/knowledge/documents/{document_id}", response_model=Msg, dependencies=[KnowledgeWrite]
)
async def delete_document(document_id: str, principal: Member, session: Db):
    await knowledge_service.delete_document(
        session, principal.workspace.id, document_id, actor=_actor(principal)
    )
    return Msg(message="Document deleted")


@router.post(
    "/knowledge/documents/{document_id}/retry",
    response_model=DocumentOut,
    dependencies=[KnowledgeWrite],
)
async def retry_document(document_id: str, principal: Member, session: Db):
    document = await knowledge_service.retry_document(
        session, principal.workspace.id, document_id, actor=_actor(principal)
    )
    return DocumentOut.model_validate(document)


# --- search -----------------------------------------------------------------


@router.post("/knowledge/search", response_model=SearchResponse, dependencies=[KnowledgeRead])
async def search_knowledge(body: SearchRequest, principal: Member, session: Db):
    return await knowledge_service.search(
        session,
        principal.workspace.id,
        body.query,
        k=body.k,
        source_ids=body.source_ids,
    )
