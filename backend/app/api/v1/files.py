"""Workspace file upload/serving (message attachments, avatars, article images).

Uploads return a storage key + metadata; domain objects (messages, articles)
embed that metadata. Serving is member-authenticated **and** namespace-scoped:
private objects live under `ws/{workspace_id}/…` and this route refuses any key
outside the caller's own namespace, so membership in one workspace never grants
a read in another even when a key leaks.

`?public=true` opts an upload into the DAP media namespace instead: images/video
only, stored under `public/{workspace_id}/…` and served without auth from
`/api/widget/media/…` (tour steps render on the customer's site, where no Stept
session exists). Everything else is unchanged.
"""

from __future__ import annotations

import mimetypes
import posixpath

from fastapi import APIRouter, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.widget.media import PUBLIC_CONTENT_TYPES, public_url, save_public
from app.core.config import get_settings
from app.core.db import utcnow, uuid7
from app.core.deps import Db, Member
from app.core.errors import BadRequestError, NotFoundError, PayloadTooLargeError
from app.core.storage import get_storage, safe_filename

router = APIRouter()

# Conservative allow-list; knowledge ingestion has its own richer parser list.
ALLOWED_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "application/pdf",
    "text/plain",
    "text/csv",
    "text/markdown",
    "application/zip",
    "application/json",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "video/mp4",
    "audio/mpeg",
    "audio/wav",
}


class FileOut(BaseModel):
    key: str
    name: str
    size: int
    content_type: str
    url: str  # workspace-relative API path


def file_url(workspace_id: str, key: str) -> str:
    return f"/api/v1/w/{workspace_id}/files/{key}"


def private_prefix(workspace_id: str) -> str:
    return f"ws/{workspace_id}/"


async def save_private(workspace_id: str, filename: str, data: bytes) -> str:
    """Persist bytes under `ws/{workspace_id}/YYYY/MM/{uuid7}-{name}`.

    The workspace segment is what `_safe_private_key` authorizes against, so the
    key is minted here rather than by the storage backend's own `save`.
    """
    key = f"{private_prefix(workspace_id)}{utcnow():%Y/%m}/{uuid7()}-{safe_filename(filename)}"
    return (await get_storage().save_at(key, data)).key


def _safe_private_key(workspace_id: str, key: str) -> str:
    """Normalize a requested key and confirm it addresses this workspace's private
    namespace. Traversal (`..`, absolute paths, backslashes) and any other
    namespace — including another workspace's, and the public one — are rejected."""
    if not key or key.startswith("/") or "\\" in key:
        raise NotFoundError("File not found")
    normalized = posixpath.normpath(key)
    if not normalized.startswith(private_prefix(workspace_id)):
        raise NotFoundError("File not found")
    return normalized


@router.post("/files", response_model=FileOut, status_code=201)
async def upload_file(
    file: UploadFile, principal: Member, session: Db, public: bool = False
) -> FileOut:
    settings = get_settings()
    content_type = file.content_type or "application/octet-stream"
    allowed = PUBLIC_CONTENT_TYPES if public else ALLOWED_CONTENT_TYPES
    if content_type not in allowed:
        raise BadRequestError(f"File type {content_type} is not allowed")
    data = await file.read()
    if len(data) > settings.upload_limit_bytes:
        raise PayloadTooLargeError(f"Files are limited to {settings.max_upload_mb} MB")
    if not data:
        raise BadRequestError("Empty file")
    name = file.filename or "file"
    if public:
        key = await save_public(principal.workspace.id, name, data)
        url = public_url(principal.workspace.id, key)
    else:
        key = await save_private(principal.workspace.id, name, data)
        url = file_url(principal.workspace.id, key)
    return FileOut(
        key=key,
        name=name,
        size=len(data),
        content_type=content_type,
        url=url,
    )


@router.get("/files/{key:path}")
async def serve_file(key: str, principal: Member) -> Response:
    normalized = _safe_private_key(principal.workspace.id, key)
    data = await get_storage().read(normalized)
    # Content type is inferred from the extension; storage keys are sanitized.
    content_type = mimetypes.guess_type(normalized)[0] or "application/octet-stream"
    disposition = (
        "inline" if content_type.startswith(("image/", "video/", "audio/")) else "attachment"
    )
    headers = {
        "Content-Disposition": f'{disposition}; filename="{normalized.rsplit("/", 1)[-1]}"',
        # Never let a browser re-type an attachment into something scriptable.
        "X-Content-Type-Options": "nosniff",
    }
    if content_type == "image/svg+xml":
        # Uploaded SVG can carry <script> and inline handlers, and this response
        # is same-origin with the dashboard — neuter it (same policy as the
        # public DAP media route).
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return Response(content=data, media_type=content_type, headers=headers)
