"""File storage. Local disk by default; the protocol keeps S3/GCS pluggable.

Keys are relative paths like "2026/07/{uuid7}-{safe-name}". Serving always goes
through authenticated API routes — storage backends never expose public URLs.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.core.db import utcnow, uuid7
from app.core.errors import NotFoundError


@dataclass(frozen=True)
class StoredFile:
    key: str
    size: int


class Storage(Protocol):
    async def save(self, filename: str, data: bytes) -> StoredFile: ...
    async def read(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


def safe_filename(filename: str) -> str:
    name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "file"
    return name[-80:]


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def _resolve(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise NotFoundError("File not found")
        return path

    async def save(self, filename: str, data: bytes) -> StoredFile:
        now = utcnow()
        key = f"{now:%Y/%m}/{uuid7()}-{safe_filename(filename)}"
        path = self._resolve(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)
        return StoredFile(key=key, size=len(data))

    async def read(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise NotFoundError("File not found") from exc

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        await asyncio.to_thread(lambda: path.unlink(missing_ok=True))


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = LocalStorage(get_settings().storage_dir)
    return _storage


def reset_storage() -> None:
    """Test helper."""
    global _storage
    _storage = None
