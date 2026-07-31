"""Public media serving for DAP step assets (screenshots, step images/video).

Tour steps render on the customer's own site, where no Stept session exists, so
their assets need an unauthenticated URL. Exactly one storage namespace is
public — `public/{workspace_id}/…` — and this route serves nothing else: private
attachments keep their authenticated `/api/v1/w/{ws}/files/{key}` route even if
their key leaks. Keys embed a uuid7, so public objects stay unguessable.
"""

from __future__ import annotations

import mimetypes
import posixpath

from fastapi import APIRouter
from fastapi.responses import Response

from app.core.db import utcnow, uuid7
from app.core.errors import NotFoundError
from app.core.storage import get_storage, safe_filename

router = APIRouter()

# Content types allowed into the public namespace (rendered on customer sites).
PUBLIC_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "video/mp4",
}
CACHE_CONTROL = "public, max-age=86400"


def public_prefix(workspace_id: str) -> str:
    return f"public/{workspace_id}/"


def public_url(workspace_id: str, key: str) -> str:
    return f"/api/widget/media/{workspace_id}/{key}"


async def save_public(workspace_id: str, filename: str, data: bytes) -> str:
    """Persist bytes under `public/{workspace_id}/YYYY/MM/{uuid7}-{name}` and
    return the storage key.

    The namespace prefix is what makes the object publicly servable, so the key
    is minted here rather than by the backend's own `save`.
    """
    key = f"{public_prefix(workspace_id)}{utcnow():%Y/%m}/{uuid7()}-{safe_filename(filename)}"
    stored = await get_storage().save_at(key, data)
    return stored.key


def _safe_public_key(workspace_id: str, key: str) -> str:
    """Normalize a requested key and confirm it addresses this workspace's public
    namespace. Traversal (`..`, absolute paths, backslashes) is rejected."""
    if not key or key.startswith("/") or "\\" in key:
        raise NotFoundError("Media not found")
    normalized = posixpath.normpath(key)
    if not normalized.startswith(public_prefix(workspace_id)):
        raise NotFoundError("Media not found")
    return normalized


@router.get("/media/{workspace_id}/{key:path}")
async def serve_public_media(workspace_id: str, key: str) -> Response:
    """Unauthenticated, long-cached, open-CORS delivery of public assets."""
    normalized = _safe_public_key(workspace_id, key)
    data = await get_storage().read(normalized)
    content_type = mimetypes.guess_type(normalized)[0] or "application/octet-stream"
    headers = {"Cache-Control": CACHE_CONTROL, "X-Content-Type-Options": "nosniff"}
    if content_type == "image/svg+xml":
        # Uploaded SVG can carry script — neuter it on this origin.
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return Response(content=data, media_type=content_type, headers=headers)
