"""Public download surface for the Chrome recorder extension.

Unauthenticated on purpose: the artifact is the same open-source build for every
workspace and carries no workspace data, and a plain `<a download>` in the
dashboard (or a `curl` in the docs) cannot attach a bearer token. Pairing is
what is authenticated — see `POST /api/v1/w/{id}/tours/recorder-token`.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.dap.extension_release import DOWNLOAD_FILENAME, current_release

router = APIRouter(tags=["extension"])


class ExtensionReleaseOut(BaseModel):
    """Describes the recorder build the dashboard can hand out."""

    available: bool
    version: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    download_url: str | None = None
    # Where the extension should point itself — a self-hoster's backend is not
    # the extension's compiled-in default.
    api_base: str
    web_store_url: str | None = None


@router.get("/release.json", response_model=ExtensionReleaseOut)
async def extension_release(response: Response) -> ExtensionReleaseOut:
    settings = get_settings()
    response.headers["Cache-Control"] = "no-store"
    release = current_release()
    if release is None:
        return ExtensionReleaseOut(
            available=False,
            api_base=settings.public_base_url,
            web_store_url=settings.extension_web_store_url or None,
        )
    return ExtensionReleaseOut(
        available=True,
        version=release.version,
        size_bytes=release.size_bytes,
        sha256=release.sha256,
        download_url=f"{settings.public_base_url}/extension-assets/{DOWNLOAD_FILENAME}",
        api_base=settings.public_base_url,
        web_store_url=settings.extension_web_store_url or None,
    )


@router.get(f"/{DOWNLOAD_FILENAME}")
async def download_extension() -> FileResponse:
    release = current_release()
    if release is None:
        raise NotFoundError("The recorder extension has not been built yet")
    return FileResponse(
        release.path,
        media_type="application/zip",
        filename=DOWNLOAD_FILENAME,
        headers={"Cache-Control": "no-store"},
    )
