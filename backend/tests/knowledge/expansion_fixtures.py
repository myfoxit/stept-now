"""Deterministic corpus seeding for the ranking tests.

Documents are created through the service layer inside an explicit
``session_scope()`` commit rather than over HTTP. The HTTP path commits in a
dependency teardown *after* the response is produced, so a 201 does not
guarantee the row landed — under load (an ingest task from the previous test
still holding the shared SQLite write lock) it occasionally does not, and the
ranking assertion then fails for a reason that has nothing to do with ranking.

The HTTP paste/upload endpoints are covered by `test_knowledge_api.py` and
`test_batch_upload.py`; these tests only need a corpus.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import session_scope
from app.core.events import Actor
from app.core.queue import enqueue
from app.models.knowledge import Document, KnowledgeSource
from app.services import knowledge as knowledge_service
from tests.conftest import drain_tasks


async def seed_corpus(
    workspace_id: str,
    docs: list[tuple[str, str]],
    *,
    source_name: str = "Docs",
    config: dict | None = None,
) -> str:
    """Create one source with `docs` (title, content) and index them. Returns source id."""
    async with session_scope() as session:
        source = await knowledge_service.create_source(
            session,
            workspace_id,
            actor=Actor.system(),
            type="text",
            name=source_name,
            config=config or {},
        )
        for title, content in docs:
            await knowledge_service.add_document_from_text(
                session, workspace_id, source, actor=Actor.system(), title=title, content=content
            )
        await session.commit()
        source_id = source.id
    await wait_indexed(workspace_id, len(docs))
    return source_id


async def add_source(workspace_id: str, *, name: str, config: dict | None = None) -> str:
    async with session_scope() as session:
        source = await knowledge_service.create_source(
            session,
            workspace_id,
            actor=Actor.system(),
            type="text",
            name=name,
            config=config or {},
        )
        await session.commit()
        return source.id


async def add_doc(workspace_id: str, source_id: str, *, title: str, content: str) -> None:
    async with session_scope() as session:
        source = await session.get(KnowledgeSource, source_id)
        assert source is not None
        await knowledge_service.add_document_from_text(
            session, workspace_id, source, actor=Actor.system(), title=title, content=content
        )
        await session.commit()


async def wait_indexed(workspace_id: str, expected: int, *, attempts: int = 10) -> None:
    """Drain until `expected` documents are indexed, re-triggering stragglers.

    One `drain_tasks()` is not always enough: a failed ingest task is re-enqueued
    behind a backoff, so the queue can read as empty while a retry is pending.
    """
    rows: list[tuple[str, str]] = []
    for _ in range(attempts):
        await drain_tasks()
        async with session_scope() as session:
            rows = [
                (document_id, status)
                for document_id, status in (
                    await session.execute(
                        select(Document.id, Document.status).where(
                            Document.workspace_id == workspace_id
                        )
                    )
                ).all()
            ]
        if sum(1 for _id, status in rows if status == "indexed") >= expected:
            return
        for document_id, status in rows:
            if status != "indexed":
                await enqueue("ingest_document", document_id=document_id)
        await asyncio.sleep(0.05)
    raise AssertionError(f"documents never finished indexing: {rows}")
