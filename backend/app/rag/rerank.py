"""LLM rerank/selection pass over fused retrieval results.

Onyx-style relevance selection (docs/research/onyx-gaps.md §5): after hybrid
retrieval, one cheap LLM call ranks the widened candidate set; the reply is a
JSON array of 1-based candidate numbers, most relevant first. Selection only
reorders — unselected candidates are appended in fused order, never silently
discarded below k — and ANY failure (timeout, provider error, garbage reply)
falls back to the fused order. This function never raises.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import ChatMessage, ChatRequest
from app.ai.registry import resolve_chat
from app.core.logging import log

if TYPE_CHECKING:
    from app.rag.retrieval import RetrievedChunk

logger = log("rag.rerank")

RERANK_CANDIDATES = 20
RERANK_TIMEOUT_SECONDS = 20.0
SNIPPET_CHARS = 300

_SYSTEM_PROMPT = "You rank search results for relevance."
_JSON_ARRAY_RE = re.compile(r"\[.*?\]", re.DOTALL)
_INT_RE = re.compile(r"\d+")


def _build_prompt(query: str, results: list[RetrievedChunk]) -> str:
    lines = [f"Query: {query}", "", "Search results:"]
    for number, result in enumerate(results, start=1):
        snippet = " ".join(result.content[:SNIPPET_CHARS].split())
        lines.append(f"{number}. {result.title}: {snippet}")
    lines += [
        "",
        "Reply with ONLY a JSON array of the numbers of the relevant results, "
        "most relevant first (e.g. [3,1,5]).",
    ]
    return "\n".join(lines)


def _parse_selection(reply: str, candidate_count: int) -> list[int]:
    """Extract 1-based candidate numbers from the model reply, defensively.

    First the first ``[...]`` block is tried as JSON; failing that, all integers
    in the reply are taken in order. Duplicates and out-of-range numbers drop.
    """
    numbers: list[int] = []
    match = _JSON_ARRAY_RE.search(reply)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, bool):
                        continue
                    if isinstance(item, (int, float)):
                        numbers.append(int(item))
                    elif isinstance(item, str) and item.strip().isdigit():
                        numbers.append(int(item.strip()))
        except ValueError:
            numbers = []
    if not numbers:
        numbers = [int(token) for token in _INT_RE.findall(reply)]

    selected: list[int] = []
    seen: set[int] = set()
    for number in numbers:
        if 1 <= number <= candidate_count and number not in seen:
            seen.add(number)
            selected.append(number)
    return selected


async def rerank_results(
    session: AsyncSession,
    workspace_id: str,
    query: str,
    results: list[RetrievedChunk],
    *,
    k: int,
) -> list[RetrievedChunk]:
    """Reorder fused candidates via one LLM selection call; return the top k."""
    if len(results) <= 1:
        return results[:k]
    try:
        provider, model_key = await resolve_chat(session, workspace_id, None)
        request = ChatRequest(
            model=model_key,
            messages=[
                ChatMessage.system(_SYSTEM_PROMPT),
                ChatMessage.user(_build_prompt(query, results)),
            ],
            temperature=0.0,
            max_tokens=200,
        )
        result = await asyncio.wait_for(provider.generate(request), timeout=RERANK_TIMEOUT_SECONDS)
        selected = _parse_selection(result.content or "", len(results))
        if not selected:
            logger.debug("rerank reply unusable — keeping fused order")
            return results[:k]
        reordered = [results[number - 1] for number in selected]
        chosen = {number - 1 for number in selected}
        reordered.extend(r for index, r in enumerate(results) if index not in chosen)
        return reordered[:k]
    except Exception:
        logger.warning(
            "rerank failed for workspace %s — keeping fused order", workspace_id, exc_info=True
        )
        return results[:k]
