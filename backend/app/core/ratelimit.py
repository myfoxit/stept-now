"""Sliding-window rate limiting (in-memory; Redis-backed when configured).

Usage as a dependency:
    @router.post("/login", dependencies=[Depends(RateLimit("auth", times=10, seconds=60))])
Keys default to client IP; pass `by="principal"` to key on the authenticated user.

With `STEPT_REDIS_URL` set the counter lives in Redis, so a limit means the same
thing however many uvicorn workers or hosts serve the traffic. Without it each
process keeps its own window and the effective limit is multiplied by the worker
count — fine for single-process dev, misleading for a fronted deployment.

`X-Forwarded-For` is honoured only when `trusted_proxy_hops` says a proxy sits in
front of us. Trusting it unconditionally would let every request claim a fresh
identity and make the limits decorative.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Literal

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.core.logging import log

logger = log("ratelimit")

_windows: dict[str, deque[float]] = {}
_redis: Any | None = None


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


def _redis_client() -> Any:
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def _check_redis(key: str, times: int, seconds: float) -> bool:
    """INCR + EXPIRE fixed window — cheaper than a sorted set and accurate enough
    for abuse control. If Redis is unreachable we fall back to the local window
    instead of failing the request open."""
    try:
        pipe = _redis_client().pipeline()
        pipe.incr(key)
        pipe.expire(key, int(seconds) + 1, nx=True)
        count, _ = await pipe.execute()
        return int(count) <= times
    except Exception:  # noqa: BLE001 — Redis availability must not 500 a request
        logger.warning("redis rate-limit check failed for %s; using local window", key)
        return _check_memory(key, times, seconds)


def client_identity(request: Request) -> str:
    """Remote address, reading X-Forwarded-For only from behind a trusted proxy."""
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        chain = [
            part.strip()
            for part in request.headers.get("x-forwarded-for", "").split(",")
            if part.strip()
        ]
        if chain:
            # Count from the right: the rightmost `hops` entries were appended by
            # our own proxies, so the real client sits just left of them. A
            # spoofed prefix can only impersonate, never escape, its own bucket.
            return chain[max(0, len(chain) - hops)]
    return request.client.host if request.client else "unknown"


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
            ident = client_identity(request)
        key = f"rl:{self.scope}:{ident}"
        allowed = (
            await _check_redis(key, self.times, self.seconds)
            if settings.redis_url
            else _check_memory(key, self.times, self.seconds)
        )
        if not allowed:
            logger.warning("rate limited %s", key)
            raise RateLimitedError("Too many requests, slow down")


def reset_rate_limits() -> None:
    """Test helper."""
    global _redis
    _windows.clear()
    _redis = None
