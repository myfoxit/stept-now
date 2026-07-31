"""Vendor adapters (pure httpx — no vendor SDKs) + shared HTTP/SSE plumbing.

Common rules (docs/CONTRACTS.md):
- timeout 60s total / 10s connect, NO retries here (retry policy lives above);
- failures raise `ProviderError` with `status_code` and `retryable` (429/5xx/529);
- API keys go into headers only and are never logged or echoed.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.ai.base import ProviderError

TIMEOUT = httpx.Timeout(60.0, connect=10.0)

_RETRYABLE_STATUSES = frozenset({408, 429, 529})


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT)


def _error_message(body: bytes | str) -> str:
    """Best-effort human message from a provider error body (truncated)."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            text = error["message"]
        elif isinstance(error, str):
            text = error
        elif isinstance(data.get("message"), str):
            text = data["message"]
    return text.strip()[:300]


def error_from_status(provider: str, status_code: int, body: bytes | str) -> ProviderError:
    retryable = status_code in _RETRYABLE_STATUSES or status_code >= 500
    return ProviderError(
        f"{provider}: HTTP {status_code}: {_error_message(body)}",
        status_code=status_code,
        retryable=retryable,
    )


def network_error(provider: str, exc: httpx.HTTPError) -> ProviderError:
    # Transport-level failure (DNS, refused, timeout): no response, worth retrying.
    return ProviderError(
        f"{provider}: network error: {type(exc).__name__}", status_code=None, retryable=True
    )


async def iter_sse(response: httpx.Response) -> AsyncIterator[tuple[str | None, str]]:
    """Yield `(event, data)` per SSE event, robust to chunk boundaries.

    Chunks may split lines (even mid-JSON): buffer text and only consume
    complete `\\n`-terminated lines. Multi-line `data:` fields are joined per
    the SSE spec; comment lines (`:`) and unknown fields are ignored.
    """
    buffer = ""
    event: str | None = None
    data_lines: list[str] = []
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")
            if not line:
                if data_lines:
                    yield event, "\n".join(data_lines)
                event, data_lines = None, []
            elif line.startswith(":"):
                continue
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
    if data_lines:  # stream ended without a trailing blank line
        yield event, "\n".join(data_lines)


def parse_json_object(data: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(data)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_tool_args(raw: str | None) -> dict[str, Any]:
    """Parse tool-call arguments; malformed/partial JSON degrades to {} (raw kept)."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
