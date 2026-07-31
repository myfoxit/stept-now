"""Pagination envelopes.

- `CursorPage` for feeds (conversations, messages): opaque cursor, stable under writes.
- `OffsetPage` for admin tables: page/limit with total count.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import BaseModel

from app.core.errors import BadRequestError


class CursorPage[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


class OffsetPage[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


def encode_cursor(*parts: Any) -> str:
    raw = json.dumps(list(parts), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, expected_len: int) -> list[Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        parts = json.loads(base64.urlsafe_b64decode(padded))
    except Exception as exc:
        raise BadRequestError("Malformed cursor") from exc
    if not isinstance(parts, list) or len(parts) != expected_len:
        raise BadRequestError("Malformed cursor")
    return parts


def clamp_limit(limit: int | None, default: int = 25, maximum: int = 100) -> int:
    if limit is None:
        return default
    return max(1, min(limit, maximum))
