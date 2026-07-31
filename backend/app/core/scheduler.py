"""In-process periodic scheduler.

Domain modules register jobs at import time with
``@scheduled("name", every_seconds=N)``. The app lifespan runs one loop that
ticks every ``settings.scheduler_tick_seconds`` and executes due jobs
sequentially; jobs must be quick — anything heavy looks up due rows and
enqueues real work onto the task queue (`app.core.queue`).

Tests never run the loop (disabled under env=test): they call the registered
job functions directly, or `run_due(now=...)` with a pinned clock.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.db import utcnow
from app.core.logging import log

logger = log("scheduler")

JobFn = Callable[[], Awaitable[None]]


@dataclass
class ScheduledJob:
    name: str
    fn: JobFn
    every: timedelta
    last_run_at: datetime | None = None


JOBS: dict[str, ScheduledJob] = {}


def scheduled(name: str, *, every_seconds: float) -> Callable[[JobFn], JobFn]:
    """Register a periodic job. Re-registering the same function is a no-op."""

    def decorator(fn: JobFn) -> JobFn:
        existing = JOBS.get(name)
        if existing is not None and existing.fn is not fn:
            raise RuntimeError(f"duplicate scheduled job: {name}")
        JOBS[name] = ScheduledJob(name=name, fn=fn, every=timedelta(seconds=every_seconds))
        return fn

    return decorator


async def run_due(now: datetime | None = None) -> list[str]:
    """Run every job whose interval has elapsed; returns the names that ran.

    Job failures are logged, never propagated — one broken job must not stall
    the others. `last_run_at` advances even on failure (fixed cadence, no
    hot-retry loop; the task queue owns retries for real work).
    """
    now = now or utcnow()
    ran: list[str] = []
    for job in list(JOBS.values()):
        if job.last_run_at is not None and now - job.last_run_at < job.every:
            continue
        job.last_run_at = now
        ran.append(job.name)
        try:
            await job.fn()
        except Exception:
            logger.exception("scheduled job %s failed", job.name)
    return ran


class Scheduler:
    """Lifespan wrapper: one asyncio loop task ticking `run_due`."""

    def __init__(self, tick_seconds: float):
        self._tick = tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop(), name="scheduler")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            await run_due()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick)

    async def stop(self) -> None:
        if self._task is not None:
            self._stop.set()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


def reset_jobs_state() -> None:
    """Test helper: forget last-run times (registrations survive)."""
    for job in JOBS.values():
        job.last_run_at = None
