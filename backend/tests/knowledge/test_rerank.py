"""LLM rerank/selection pass: reorder per model reply, graceful fallbacks."""

from __future__ import annotations

import asyncio

import pytest

from app.ai.base import ChatRequest, ChatResult, Usage
from app.rag import rerank as rerank_module
from app.rag.rerank import rerank_results
from app.rag.retrieval import RetrievedChunk


class StubProvider:
    """ChatProvider stand-in with a scripted reply / delay / failure."""

    def __init__(self, reply: str = "[1]", *, delay: float = 0.0, error: Exception | None = None):
        self.reply = reply
        self.delay = delay
        self.error = error
        self.requests: list[ChatRequest] = []

    async def generate(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return ChatResult(content=self.reply, tool_calls=[], finish_reason="stop", usage=Usage())

    def stream(self, request: ChatRequest):  # pragma: no cover — unused by rerank
        raise NotImplementedError


def _install(monkeypatch: pytest.MonkeyPatch, provider: StubProvider) -> None:
    async def _resolve(session, workspace_id, model_ref=None):
        return provider, "stub-model"

    monkeypatch.setattr(rerank_module, "resolve_chat", _resolve)


def _results(n: int) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=f"chunk-{i}",
            document_id=f"doc-{i}",
            content=f"Candidate content number {i}",
            score=round(1.0 - i * 0.05, 4),
            title=f"Doc {i}",
            url=None,
            ord=0,
        )
        for i in range(1, n + 1)
    ]


def _ids(results: list[RetrievedChunk]) -> list[str]:
    return [r.chunk_id for r in results]


async def test_single_result_skips_llm(monkeypatch):
    provider = StubProvider(error=AssertionError("must not be called"))
    _install(monkeypatch, provider)
    results = _results(1)
    out = await rerank_results(None, "ws", "query", results, k=5)
    assert out == results
    assert provider.requests == []


async def test_reorders_per_selection(monkeypatch):
    provider = StubProvider("[2, 1]")
    _install(monkeypatch, provider)
    results = _results(3)
    out = await rerank_results(None, "ws", "install widget", results, k=3)
    assert _ids(out) == ["chunk-2", "chunk-1", "chunk-3"]
    # one call: system + user prompt carrying the query and numbered candidates
    (request,) = provider.requests
    assert request.messages[0].role == "system"
    assert "install widget" in (request.messages[1].content or "")
    assert "2. Doc 2" in (request.messages[1].content or "")


async def test_unselected_candidates_appended_in_fused_order(monkeypatch):
    _install(monkeypatch, StubProvider("[3]"))
    results = _results(4)
    out = await rerank_results(None, "ws", "q", results, k=4)
    # selection reorders, never discards below k
    assert _ids(out) == ["chunk-3", "chunk-1", "chunk-2", "chunk-4"]


async def test_selection_dedupes_and_drops_out_of_range(monkeypatch):
    _install(monkeypatch, StubProvider("[2, 9, 2, 1]"))
    results = _results(3)
    out = await rerank_results(None, "ws", "q", results, k=3)
    assert _ids(out) == ["chunk-2", "chunk-1", "chunk-3"]


async def test_integers_extracted_when_reply_is_not_json(monkeypatch):
    _install(monkeypatch, StubProvider("Most relevant: 3, then 1."))
    results = _results(3)
    out = await rerank_results(None, "ws", "q", results, k=2)
    assert _ids(out) == ["chunk-3", "chunk-1"]


async def test_garbage_reply_falls_back_to_fused_order(monkeypatch):
    _install(monkeypatch, StubProvider("none of these look relevant to me"))
    results = _results(4)
    out = await rerank_results(None, "ws", "q", results, k=2)
    assert _ids(out) == ["chunk-1", "chunk-2"]


async def test_provider_error_falls_back(monkeypatch):
    _install(monkeypatch, StubProvider(error=RuntimeError("provider exploded")))
    results = _results(3)
    out = await rerank_results(None, "ws", "q", results, k=2)
    assert _ids(out) == ["chunk-1", "chunk-2"]


async def test_timeout_falls_back(monkeypatch):
    _install(monkeypatch, StubProvider("[3, 2, 1]", delay=0.3))
    monkeypatch.setattr(rerank_module, "RERANK_TIMEOUT_SECONDS", 0.05)
    results = _results(3)
    out = await rerank_results(None, "ws", "q", results, k=3)
    assert _ids(out) == ["chunk-1", "chunk-2", "chunk-3"]


async def test_search_chunks_rerank_end_to_end(seeded_ctx, monkeypatch):
    from app.core.db import get_session_factory
    from app.rag.retrieval import search_chunks

    provider = StubProvider("[2, 1]")
    _install(monkeypatch, provider)

    async with get_session_factory()() as session:
        fused = await search_chunks(
            session, seeded_ctx.id, "refund", k=3, expand_neighbors=False, rerank=False
        )
        reranked = await search_chunks(
            session, seeded_ctx.id, "refund", k=3, expand_neighbors=False, rerank=True
        )
    assert len(fused) >= 2, "expected multiple fused results on seeded docs"
    # rerank=False never touches the LLM; rerank=True swaps candidates 1 and 2
    assert reranked[0].chunk_id == fused[1].chunk_id
    assert reranked[1].chunk_id == fused[0].chunk_id
    if len(fused) >= 3:
        assert reranked[2].chunk_id == fused[2].chunk_id
    assert len(provider.requests) == 1
