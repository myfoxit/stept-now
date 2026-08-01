"""Locate and package the built Chrome recorder extension.

The recorder is the primary way tours get authored, so the dashboard needs to
hand a user a working `.zip` without them ever cloning the repo or running a
build. Two shapes of build output are accepted:

* `extension/dist/stept-extension-<version>-chrome.zip` — what `wxt zip` emits.
* `extension/dist/chrome-mv3/` — the unpacked build from a plain `wxt build`.

When only the unpacked directory exists we pack it ourselves and cache the
result, so a dev who ran `make build` (which does not zip) still gets a
download instead of a dead button.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import log

logger = log("dap.extension_release")

# backend/app/dap/extension_release.py → repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DIST = _REPO_ROOT / "extension" / "dist"
_UNPACKED = _DIST / "chrome-mv3"
_PACKED_CACHE = _DIST / ".stept-recorder-cache.zip"

DOWNLOAD_FILENAME = "stept-recorder.zip"


@dataclass(frozen=True)
class ExtensionRelease:
    """Everything the dashboard needs to describe/serve the recorder build."""

    version: str
    size_bytes: int
    sha256: str
    path: Path


def _read_version() -> str:
    manifest = _UNPACKED / "manifest.json"
    if manifest.is_file():
        try:
            return str(json.loads(manifest.read_text())["version"])
        except (json.JSONDecodeError, KeyError, OSError):
            logger.warning("unreadable extension manifest at %s", manifest)
    return "unknown"


def _wxt_zip() -> Path | None:
    """The newest `wxt zip` artifact, if one was built."""
    candidates = sorted(
        (p for p in _DIST.glob("*.zip") if p != _PACKED_CACHE),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _newest_source_mtime() -> float:
    return max((p.stat().st_mtime for p in _UNPACKED.rglob("*") if p.is_file()), default=0.0)


def _pack_unpacked() -> Path | None:
    """Zip `chrome-mv3/`, reusing the cache when it is newer than the build."""
    if not (_UNPACKED / "manifest.json").is_file():
        return None
    newest = _newest_source_mtime()
    if _PACKED_CACHE.is_file() and _PACKED_CACHE.stat().st_mtime >= newest:
        return _PACKED_CACHE

    tmp = _PACKED_CACHE.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry in sorted(_UNPACKED.rglob("*")):
                if entry.is_file():
                    archive.write(entry, entry.relative_to(_UNPACKED).as_posix())
        tmp.replace(_PACKED_CACHE)
    except OSError:
        logger.exception("could not pack the unpacked extension build")
        tmp.unlink(missing_ok=True)
        return None
    return _PACKED_CACHE


def current_release() -> ExtensionRelease | None:
    """The servable recorder build, or None when the extension was never built."""
    if not _DIST.is_dir():
        return None
    archive = _wxt_zip() or _pack_unpacked()
    if archive is None or not archive.is_file():
        return None
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return ExtensionRelease(
        version=_read_version(),
        size_bytes=archive.stat().st_size,
        sha256=digest,
        path=archive,
    )
