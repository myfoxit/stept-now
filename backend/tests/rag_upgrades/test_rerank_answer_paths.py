"""Rerank is ON in the answer paths (agent search_knowledge tool + reply copilot),
self-gated to fused candidate sets larger than five, and it degrades to the
fused order on any rerank failure. Interactive search stays opt-in."""

from __future__ import annotations

from typing import Any

from app.agents import copilot as copilot_module
from app.agents import tools as tool_registry
from app.agents.tools import ToolContext
from app.rag import rerank as rerank_module
from app.rag.retrieval import MIN_RERANK_CANDIDATES
from tests.rag_upgrades.conftest import build_corpus, corpus_docs, make_tool_ctx, seed_conversation


class RerankSpy:
    """Deterministic, observable stand-in for the LLM rerank pass."""

    def __init__(self):
        self.calls: list[tuple[str, int, int]] = []

    async def __call__(self, session, workspace_id, query, results, *, k):
        self.calls.append((query, len(results), k))
        return list(reversed(results))[:k]


async def run_search_tool(ctx: ToolContext, query: str) -> dict[str, Any]:
    outcome = await tool_registry.execute_tool(ctx, "search_knowledge", {"query": query})
    assert not outcome.is_error
    return outcome.result


# --- agent tool path ---------------------------------------------------------


async def test_agent_tool_reranks_when_more_than_five_candidates(session, ws, monkeypatch):
    await build_corpus(session, ws, corpus_docs(8))
    spy = RerankSpy()
    monkeypatch.setattr(rerank_module, "rerank_results", spy)

    ctx = make_tool_ctx(session, ws)
    payload = run_first = await run_search_tool(ctx, "billing plan")

    assert len(spy.calls) == 1
    _query, candidate_count, k = spy.calls[0]
    assert candidate_count > MIN_RERANK_CANDIDATES
    assert k == 6
    assert payload["results"], "reranked search must still return results"
    assert ctx.run.citations, "citations must be recorded on the run"

    # Deterministic: the same query yields byte-identical payloads.
    ctx2 = make_tool_ctx(session, ws)
    run_second = await run_search_tool(ctx2, "billing plan")
    assert run_first == run_second
    assert ctx.run.citations == ctx2.run.citations


async def test_agent_tool_skips_rerank_at_five_or_fewer_candidates(session, ws, monkeypatch):
    await build_corpus(session, ws, corpus_docs(4))
    spy = RerankSpy()
    monkeypatch.setattr(rerank_module, "rerank_results", spy)

    ctx = make_tool_ctx(session, ws)
    payload = await run_search_tool(ctx, "billing plan")

    assert spy.calls == [], "≤5 candidates must not spend an LLM call"
    assert payload["results"], "the fused results still come back"


async def test_agent_tool_is_stable_with_the_real_mock_provider(session, ws):
    """No spy: the real rerank pass against the deterministic mock provider."""
    await build_corpus(session, ws, corpus_docs(8))
    first = await run_search_tool(make_tool_ctx(session, ws), "billing plan")
    second = await run_search_tool(make_tool_ctx(session, ws), "billing plan")
    assert first == second
    assert first["results"]


async def test_agent_tool_degrades_to_fused_order_when_rerank_fails(session, ws, monkeypatch):
    from app.rag.context import DEFAULT_MAX_TOKENS, build_context
    from app.rag.retrieval import search_chunks

    await build_corpus(session, ws, corpus_docs(8))

    async def explode(*args, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(rerank_module, "resolve_chat", explode)

    ctx = make_tool_ctx(session, ws)
    payload = await run_search_tool(ctx, "billing plan")
    assert payload["results"], "a rerank failure must never lose the answer"

    expected = await search_chunks(session, ws, "billing plan", k=6, rerank=False, history=[])
    expected_context = build_context(expected, "billing plan", max_tokens=DEFAULT_MAX_TOKENS)
    assert [item["title"] for item in payload["results"]] == [
        citation.title for citation in expected_context.citations
    ], "failed rerank must fall back to exactly the un-reranked order"


# --- copilot path ------------------------------------------------------------


async def test_copilot_reranks_when_more_than_five_candidates(session, ws, monkeypatch):
    await build_corpus(session, ws, corpus_docs(8))
    conversation = await seed_conversation(session, ws, "Tell me about billing plans please")
    spy = RerankSpy()
    monkeypatch.setattr(rerank_module, "rerank_results", spy)

    suggestion = await copilot_module.suggest_reply(session, conversation, "Morgan")

    assert len(spy.calls) == 1
    assert spy.calls[0][1] > MIN_RERANK_CANDIDATES
    assert spy.calls[0][2] == 5  # the copilot's k
    assert suggestion["citations"], "grounded draft must carry citations"


async def test_copilot_skips_rerank_at_five_or_fewer_candidates(session, ws, monkeypatch):
    await build_corpus(session, ws, corpus_docs(3))
    conversation = await seed_conversation(session, ws, "Tell me about billing plans please")
    spy = RerankSpy()
    monkeypatch.setattr(rerank_module, "rerank_results", spy)

    suggestion = await copilot_module.suggest_reply(session, conversation, "Morgan")
    assert spy.calls == []
    assert suggestion["citations"]


# --- interactive endpoints stay opt-in --------------------------------------


async def test_playground_search_does_not_rerank_by_default(client, workspace_ctx, monkeypatch):
    from app.core.db import get_session_factory

    async with get_session_factory()() as setup:
        await build_corpus(setup, workspace_ctx.id, corpus_docs(8))
        await setup.commit()

    spy = RerankSpy()
    monkeypatch.setattr(rerank_module, "rerank_results", spy)
    response = await client.post(
        f"{workspace_ctx.base}/knowledge/search",
        json={"query": "billing plan", "k": 6},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"]
    assert spy.calls == [], "interactive search must stay opt-in for rerank"
