"""Fixtures for the RAG upgrade wave: direct-DB corpora + a throwaway PG session.

`build_corpus` inserts sources/documents/chunks straight into the DB (embedding
via the local hash embedder so the dense leg works on SQLite) — much cheaper
than the ingestion pipeline, and it lets a test decide exactly how many fused
candidates a query will produce.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools import ToolContext
from app.ai.local import LocalHashEmbedder
from app.core.db import uuid7
from app.core.events import Actor
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.knowledge import Chunk, Document, KnowledgeSource
from app.models.message import Message
from app.models.workspace import Workspace


async def build_corpus(
    session: AsyncSession,
    workspace_id: str,
    docs: list[tuple[str, list[str]]],
    *,
    source_type: str = "text",
    source_name: str | None = None,
    embed: bool = True,
) -> tuple[str, dict[str, str]]:
    """[(title, [chunk texts]), …] → (source_id, {title: document_id}).

    ``embed=False`` leaves embeddings NULL (fine for FTS/trgm-only PG tests and
    immune to whatever vector dimension an existing PG schema was created with).
    """
    embedder = LocalHashEmbedder()
    source = KnowledgeSource(
        workspace_id=workspace_id,
        type=source_type,
        name=source_name or f"Corpus {uuid7()[:8]}",
    )
    session.add(source)
    await session.flush()
    document_ids: dict[str, str] = {}
    for title, chunk_texts in docs:
        document = Document(
            workspace_id=workspace_id, source_id=source.id, title=title, status="indexed"
        )
        session.add(document)
        await session.flush()
        vectors = await embedder.embed(list(chunk_texts)) if embed else [None] * len(chunk_texts)
        for ord_, (content, vector) in enumerate(zip(chunk_texts, vectors, strict=True)):
            session.add(
                Chunk(
                    workspace_id=workspace_id,
                    document_id=document.id,
                    ord=ord_,
                    content=content,
                    embedding=vector,
                    meta={"title": title},
                )
            )
        document_ids[title] = document.id
    await session.flush()
    return source.id, document_ids


def corpus_docs(count: int, keyword: str = "billing") -> list[tuple[str, list[str]]]:
    """`count` one-chunk documents that all match `keyword` (candidate-count dial)."""
    return [
        (
            f"Doc {index} about {keyword}",
            [f"Fact {index}: our {keyword} plan number {index} renews monthly."],
        )
        for index in range(1, count + 1)
    ]


@pytest.fixture
async def ws(session) -> str:
    """A bare workspace row — enough for retrieval + search-analytics FKs."""
    workspace = Workspace(name="RAG Upgrades", slug=f"ragup-{uuid7()}")
    session.add(workspace)
    await session.flush()
    return workspace.id


def make_tool_ctx(session: AsyncSession, workspace_id: str) -> ToolContext:
    """Unpersisted agent/run: the search tool only reads their attributes."""
    agent = Agent(
        workspace_id=workspace_id,
        name="Sage",
        status="live",
        model_ref="mock",
        system_prompt="",
        temperature=None,
        settings={"retrieval": {"enabled": True, "k": 6, "source_ids": None}},
        tools=[],
    )
    run = AgentRun(
        id=uuid7(),
        workspace_id=workspace_id,
        conversation_id=uuid7(),
        agent_id=uuid7(),
        status="running",
        input_tokens=0,
        output_tokens=0,
        citations=[],
    )
    return ToolContext(
        session=session,
        workspace_id=workspace_id,
        run=run,
        agent=agent,
        conversation=None,
        actor=Actor(type="agent", id=None, label="Sage"),
    )


async def seed_conversation(session: AsyncSession, workspace_id: str, text: str) -> Conversation:
    """Widget inbox + contact + one public inbound message (copilot input)."""
    inbox = Inbox(
        workspace_id=workspace_id,
        name="Widget",
        channel_type="widget",
        config={},
        widget_key=f"wk_{uuid7()}",
    )
    contact = Contact(workspace_id=workspace_id, name="Casey", email="casey@example.com")
    session.add_all([inbox, contact])
    await session.flush()
    conversation = Conversation(
        workspace_id=workspace_id,
        number=1,
        inbox_id=inbox.id,
        contact_id=contact.id,
        status="open",
    )
    session.add(conversation)
    await session.flush()
    session.add(
        Message(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            direction="in",
            visibility="public",
            author_type="contact",
            author_id=contact.id,
            author_name="Casey",
            content=text,
        )
    )
    await session.flush()
    return conversation


@pytest.fixture
async def pg_session(monkeypatch):
    """Session against real Postgres with a throwaway workspace (mark: pg).

    Mirrors tests/knowledge/test_retrieval_pg.py so PG-only behavior is exercised
    the same way across suites.
    """
    monkeypatch.setenv("STEPT_DATABASE_URL", os.environ["STEPT_TEST_PG_URL"])
    from app.core.config import reset_settings_cache
    from app.core.db import dispose_engine, get_session_factory, init_db

    reset_settings_cache()
    await dispose_engine()  # drop any engine built against sqlite
    await init_db()

    from app.models.workspace import Workspace

    workspace = Workspace(name="PG RAG Upgrades", slug=f"pg-ragup-{uuid7()}")
    async with get_session_factory()() as session:
        session.add(workspace)
        await session.commit()
        workspace_id = workspace.id
        try:
            yield session, workspace_id
        finally:
            await session.rollback()
            deleted = await session.get(Workspace, workspace_id)
            if deleted is not None:
                await session.delete(deleted)  # FK cascade removes sources/docs/chunks
                await session.commit()
    await dispose_engine()
