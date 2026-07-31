"""Background task for the agent engine.

``execute_agent_run {run_id}`` runs (or resumes) one agent run in its own unit of
work. It is idempotent: the engine claims the run via a status+lease guard, so a
duplicate delivery or a crash-retry no-ops or safely resumes from the journal.

A per-event-loop lock serializes agent runs within a process — the in-process /
SQLite setup shares a single DB connection where interleaved task transactions
would collide (same rationale as the RAG tasks).

Importing this module registers the task; ``app.agents.engine`` imports it at the
bottom so a single side-effect import of the engine wires up both the @on handlers
and this task.
"""

from __future__ import annotations

import asyncio
import weakref

from app.core.db import session_scope
from app.core.logging import log
from app.core.queue import MAX_ATTEMPTS, TaskContext, task
from app.models.agent_run import AgentRun

logger = log("agents")

_task_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _task_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _task_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _task_locks[loop] = lock
    return lock


@task("execute_agent_run")
async def execute_agent_run_task(ctx: TaskContext, *, run_id: str) -> None:
    from app.agents import engine

    async with _task_lock(), session_scope() as session:
        run = await session.get(AgentRun, run_id)
        if run is None:
            # The enqueuing transaction may not have committed yet — retry via backoff.
            if ctx.attempt < MAX_ATTEMPTS:
                raise RuntimeError(f"agent run {run_id} not visible yet")
            logger.warning("agent run %s never became visible", run_id)
            return
        await engine.execute_run(session, run)
