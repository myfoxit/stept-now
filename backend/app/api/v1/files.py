"""Workspace file upload/serving (message attachments, avatars, article images).

Uploads return a storage key + metadata; domain objects (messages, articles)
embed that metadata. Serving is member-authenticated; the widget re-exposes
conversation attachments through its own authenticated routes.

`?public=true` opts an upload into the DAP media namespace instead: images/video
only, stored under `public/{workspace_id}/…` and served without auth from
`/api/widget/media/…` (tour steps render on the customer's site, where no Stept
session exists). Everything else is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.widget.media import PUBLIC_CONTENT_TYPES, public_url, save_public
from app.core.config import get_settings
from app.core.deps import Db, Member
from app.core.errors import BadRequestError, PayloadTooLargeError
from app.core.storage import get_storage

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
        key = (await get_storage().save(name, data)).key
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
    data = await get_storage().read(key)
    # Content type is inferred from the extension; storage keys are sanitized.
    import mimetypes

    content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
    disposition = (
        "inline" if content_type.startswith(("image/", "video/", "audio/")) else "attachment"
    )
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'{disposition}; filename="{key.rsplit("/", 1)[-1]}"'},
    )
