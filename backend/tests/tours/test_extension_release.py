"""Serving the recorder extension build: metadata + download, packed on demand."""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from app.dap import extension_release


@pytest.fixture
def fake_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the release module at a throwaway `extension/dist`."""
    dist = tmp_path / "dist"
    unpacked = dist / "chrome-mv3"
    unpacked.mkdir(parents=True)
    monkeypatch.setattr(extension_release, "_DIST", dist)
    monkeypatch.setattr(extension_release, "_UNPACKED", unpacked)
    monkeypatch.setattr(extension_release, "_PACKED_CACHE", dist / ".cache.zip")
    return dist, unpacked


def _write_build(unpacked: Path, version: str = "0.2.0") -> None:
    (unpacked / "manifest.json").write_text(json.dumps({"version": version, "name": "Stept"}))
    (unpacked / "background.js").write_text("// worker")
    (unpacked / "icon").mkdir(exist_ok=True)
    (unpacked / "icon" / "16.png").write_bytes(b"\x89PNG\r\n\x1a\n")


async def test_release_reports_unavailable_without_a_build(client, fake_dist):
    resp = await client.get("/extension-assets/release.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["download_url"] is None
    # The pairing address is still needed to point an extension at this server.
    assert body["api_base"]


async def test_download_404s_without_a_build(client, fake_dist):
    resp = await client.get("/extension-assets/stept-recorder.zip")
    assert resp.status_code == 404


async def test_packs_the_unpacked_build_on_demand(client, fake_dist):
    _, unpacked = fake_dist
    _write_build(unpacked)

    resp = await client.get("/extension-assets/release.json")
    body = resp.json()
    assert body["available"] is True
    assert body["version"] == "0.2.0"
    assert body["size_bytes"] > 0
    assert len(body["sha256"]) == 64
    assert body["download_url"].endswith("/extension-assets/stept-recorder.zip")

    download = await client.get("/extension-assets/stept-recorder.zip")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"

    # Paths must be relative to the extension root or Chrome rejects the load,
    # and the icons the manifest references have to be inside.
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        names = set(zf.namelist())
    assert "manifest.json" in names
    assert "icon/16.png" in names
    assert not any(n.startswith("chrome-mv3/") for n in names)


async def test_prefers_the_wxt_zip_over_repacking(client, fake_dist):
    dist, unpacked = fake_dist
    _write_build(unpacked)
    shipped = dist / "stept-extension-9.9.9-chrome.zip"
    with zipfile.ZipFile(shipped, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"version": "9.9.9"}))
        zf.writestr("marker.txt", "from wxt")

    download = await client.get("/extension-assets/stept-recorder.zip")
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        assert "marker.txt" in zf.namelist()


async def test_repack_cache_refreshes_when_the_build_changes(fake_dist):
    _, unpacked = fake_dist
    _write_build(unpacked)
    first = extension_release.current_release()
    assert first is not None

    # A rebuild must not keep serving the stale cached archive.
    (unpacked / "background.js").write_text("// rebuilt, different bytes entirely")
    future = first.path.stat().st_mtime + 10
    os.utime(unpacked / "background.js", (future, future))

    second = extension_release.current_release()
    assert second is not None
    assert second.sha256 != first.sha256
