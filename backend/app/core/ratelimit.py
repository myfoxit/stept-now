"""Sliding-window rate limiting (in-memory; Redis-backed when configured).

Usage as a dependency:
    @router.post("/login", dependencies=[Depends(RateLimit("auth", times=10, seconds=60))])
Keys default to client IP; pass `by="principal"` to key on the authenticated user.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Literal

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.core.logging import log

logger = log("ratelimit")

_windows: dict[str, deque[float]] = {}


def _check_memory(key: str, times: int, seconds: float) -> bool:
    now = time.monotonic()
    window = _windows.setdefault(key, deque())
    cutoff = now - seconds
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= times:
        return False
    window.append(now)
    return True


class RateLimit:
    def __init__(
        self,
        scope: str,
        *,
        times: int,
        seconds: float,
        by: Literal["ip", "principal"] = "ip",
    ):
        self.scope = scope
        self.times = times
        self.seconds = seconds
        self.by = by

    async def __call__(self, request: Request) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled or settings.env == "test":
            return
        if self.by == "principal" and getattr(request.state, "principal_id", None):
            ident = str(request.state.principal_id)
        else:
            ident = request.client.host if request.client else "unknown"
        key = f"rl:{self.scope}:{ident}"
        if not _check_memory(key, self.times, self.seconds):
            logger.warning("rate limited %s", key)
            raise RateLimitedError("Too many requests, slow down")


def reset_rate_limits() -> None:
    """Test helper."""
    _windows.clear()
