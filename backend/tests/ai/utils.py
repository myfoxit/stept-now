"""Shared helpers for AI adapter tests: SSE fixtures for respx-mocked streams."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


def sse_response(
    body: str, *, chunk_size: int | None = None, status_code: int = 200
) -> httpx.Response:
    """Streamed SSE response. `chunk_size` forces transport chunk boundaries that
    split SSE lines (and the JSON inside them) — parsers must reassemble."""
    data = body.encode()
    chunks = (
        [data]
        if chunk_size is None
        else [data[i : i + chunk_size] for i in range(0, len(data), chunk_size)]
    )

    async def stream() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    return httpx.Response(
        status_code, headers={"content-type": "text/event-stream"}, content=stream()
    )


def data_line(obj: Any) -> str:
    """One anonymous SSE event (OpenAI / Google style)."""
    return f"data: {json.dumps(obj)}\n\n"


def event_line(event: str, obj: Any) -> str:
    """One named SSE event (Anthropic style)."""
    return f"event: {event}\ndata: {json.dumps(obj)}\n\n"


async def collect(stream: AsyncIterator[Any]) -> list[Any]:
    return [event async for event in stream]
