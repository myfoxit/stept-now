"""Background task queue.

Register tasks with `@task("name")`; enqueue with `await enqueue("name", **kwargs)`.
Default backend runs tasks in-process on the event loop (single process, with
retries) — perfect for dev/tests/small installs. With STEPT_REDIS_URL set, tasks
run on ARQ workers instead (`app/workers/arq.py`).

Task functions receive a `TaskContext` and keyword arguments, and must be
idempotent — retries and at-least-once delivery are both possible.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import log

logger = log("queue")


@dataclass
class TaskContext:
    settings: Settings
    attempt: int = 1
    meta: dict[str, Any] = field(default_factory=dict)


TaskFn = Callable[..., Awaitable[None]]

TASKS: dict[str, TaskFn] = {}

MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 0.5


def task(name: str) -> Callable[[TaskFn], TaskFn]:
    def decorator(fn: TaskFn) -> TaskFn:
        if name in TASKS and TASKS[name] is not fn:
            raise RuntimeError(f"duplicate task name: {name}")
        TASKS[name] = fn
        return fn

    return decorator


async def run_task(name: str, kwargs: dict[str, Any], attempt: int = 1) -> None:
    fn = TASKS.get(name)
    if fn is None:
        raise RuntimeError(f"unknown task: {name}")
    ctx = TaskContext(settings=get_settings(), attempt=attempt)
    await fn(ctx, **kwargs)


class TaskQueue(Protocol):
    async def enqueue(self, name: str, **kwargs: Any) -> None: ...
    async def drain(self) -> None: ...
    async def close(self) -> None: ...


class InProcessQueue:
    """Runs tasks as asyncio tasks in the API process, with retry + backoff."""

    def __init__(self) -> None:
        self._pending: set[asyncio.Task[None]] = set()

    async def enqueue(self, name: str, **kwargs: Any) -> None:
        t = asyncio.create_task(self._run_with_retry(name, kwargs), name=f"task:{name}")
        self._pending.add(t)
        t.add_done_callback(self._pending.discard)

    async def _run_with_retry(self, name: str, kwargs: dict[str, Any]) -> None:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await run_task(name, kwargs, attempt=attempt)
                return
            except Exception:
                if attempt == MAX_ATTEMPTS:
                    logger.exception("task %s failed after %d attempts", name, attempt)
                    return
                delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning("task %s attempt %d failed; retrying in %.1fs", name, attempt, delay)
                await asyncio.sleep(delay)

    async def drain(self) -> None:
        """Wait for all in-flight tasks (tests rely on this for determinism)."""
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def close(self) -> None:
        await self.drain()


class ArqQueue:
    """Enqueues onto Redis for ARQ workers (see app/workers/arq.py)."""

    def __init__(self, url: str):
        self._url = url
        self._pool: Any = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            from arq import create_pool
            from arq.connections import RedisSettings

            self._pool = await create_pool(RedisSettings.from_dsn(self._url))
        return self._pool

    async def enqueue(self, name: str, **kwargs: Any) -> None:
        pool = await self._get_pool()
        await pool.enqueue_job("dispatch_task", name, kwargs)

    async def drain(self) -> None:  # workers own execution; nothing to await here
        return

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


_queue: TaskQueue | None = None


def get_queue() -> TaskQueue:
    global _queue
    if _queue is None:
        settings = get_settings()
        _queue = ArqQueue(settings.redis_url) if settings.redis_url else InProcessQueue()
    return _queue


async def enqueue(name: str, **kwargs: Any) -> None:
    await get_queue().enqueue(name, **kwargs)


async def reset_queue() -> None:
    """Test helper."""
    global _queue
    if _queue is not None:
        await _queue.close()
    _queue = None
