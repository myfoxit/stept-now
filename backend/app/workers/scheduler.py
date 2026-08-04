"""Standalone scheduler process: `python -m app.workers.scheduler`.

The periodic jobs (SLA scans, campaign dispatch, knowledge re-sync, agent wait
sweeps) also run inside the API's lifespan, which is exactly right for a
single-process dev server. Under multiple uvicorn workers that becomes N copies
of every job — duplicate campaign sends, duplicate breach events — so a fronted
deployment sets ``STEPT_SCHEDULER_ENABLED=false`` on the API and runs one of
these instead.

The jobs themselves only look up due rows and enqueue real work onto the task
queue, so this process stays cheap; the ARQ worker does the lifting.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.logging import configure_logging, log
from app.core.pubsub import get_pubsub, reset_pubsub
from app.core.queue import get_queue, reset_queue
from app.core.scheduler import JOBS, Scheduler

logger = log("scheduler-process")


def _register_jobs() -> None:
    """Import for side effects — every @scheduled lives in a domain module."""
    import app.agents.engine  # noqa: F401
    import app.rag.tasks  # noqa: F401
    import app.services.campaigns  # noqa: F401
    import app.services.slas  # noqa: F401


async def main() -> None:
    configure_logging()
    settings = get_settings()
    _register_jobs()

    # Enqueued work must reach the ARQ workers, not this process's event loop.
    get_pubsub()
    get_queue()

    scheduler = Scheduler(settings.scheduler_tick_seconds)
    scheduler.start()
    logger.info(
        "scheduler running (tick=%ss, jobs=%s)",
        settings.scheduler_tick_seconds,
        ", ".join(sorted(JOBS)) or "none",
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        logger.info("scheduler stopping")
        await scheduler.stop()
        await reset_queue()
        await reset_pubsub()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
