"""ARQ worker entrypoint: `uv run arq app.workers.arq.WorkerSettings`.

All domain tasks register themselves via @task("name"); this worker just
dispatches by name so the API and worker share one registry.
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.queue import run_task


async def dispatch_task(ctx: dict[str, Any], name: str, kwargs: dict[str, Any]) -> None:
    attempt = int(ctx.get("job_try", 1))
    await run_task(name, kwargs, attempt=attempt)


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    # Import side effects: register every @task in the codebase.
    import app.services.email  # noqa: F401

    for module in (
        "app.rag.tasks",
        "app.agents.tasks",
        "app.channels.tasks",
        "app.automation.tasks",
        "app.services.webhooks_delivery",
    ):
        try:
            __import__(module)
        except ImportError:
            continue  # module arrives in a later wave


class WorkerSettings:
    functions = [dispatch_task]
    on_startup = startup
    max_tries = 3
    job_timeout = 600
    redis_settings: RedisSettings | None = None


_url = get_settings().redis_url
if _url:  # evaluated only when this module is imported (i.e. running the worker)
    WorkerSettings.redis_settings = RedisSettings.from_dsn(_url)
