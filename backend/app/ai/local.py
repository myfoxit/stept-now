"""Offline AI: deterministic mock chat provider + hash embedder.

These make every AI feature (RAG answers, agent runs, approval gates, copilot)
fully testable and demo-able with zero API keys. The mock provider is a
first-class provider kind ("mock"), not a test-only shim.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import AsyncIterator

from app.ai.base import (
    ChatRequest,
    ChatResult,
    StreamEvent,
    StreamFinish,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
from app.core.config import get_settings

# Scripted tool-call directive, e.g. in a user message or system prompt:
#   [[tool:search_knowledge {"query": "refunds"}]]
_TOOL_DIRECTIVE = re.compile(r"\[\[tool:([a-zA-Z0-9_-]+)\s*(\{.*?\})?\]\]", re.DOTALL)
_MOCK_REPLY = re.compile(r"MOCK_REPLY:(.+?)(?:\n|$)", re.DOTALL)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class MockChatProvider:
    """Deterministic ChatProvider.

    Behavior contract (relied on by agent-engine tests and e2e):
    1. If the latest non-tool message contains `[[tool:NAME {json}]]` and NAME is
       an available tool, call it (directives are consumed in order across turns).
    2. Else if tool results are present, answer extractively from them
       (first sentences + [1] citation marker when a source id is present).
    3. Else if the system prompt contains `MOCK_REPLY: ...`, reply with that.
    4. Else reply with a deterministic echo of the latest user message.
    """

    def _pending_directive(self, request: ChatRequest) -> ToolCall | None:
        available = {t.name for t in request.tools}
        directives: list[tuple[str, str | None]] = []
        for message in request.messages:
            if message.role in ("user", "system") and message.content:
                directives.extend(_TOOL_DIRECTIVE.findall(message.content))
        called = {tc.id for m in request.messages if m.role == "assistant" for tc in m.tool_calls}
        for index, (name, args) in enumerate(directives):
            call_id = f"mock-call-{index}-{name}"
            if call_id in called or name not in available:
                continue
            try:
                parsed = json.loads(args) if args else {}
            except json.JSONDecodeError:
                parsed = {}
            return ToolCall(id=call_id, name=name, input=parsed, raw_input=args)
        return None

    def _compose_text(self, request: ChatRequest) -> str:
        tool_results = [m for m in request.messages if m.role == "tool" and m.content]
        if tool_results:
            snippets: list[str] = []
            for result in tool_results[-2:]:
                content = result.content or ""
                try:
                    data = json.loads(content)
                    if isinstance(data, dict) and "results" in data:
                        for item in data["results"][:2]:
                            text = str(item.get("content", item.get("text", "")))[:280]
                            if text:
                                snippets.append(text)
                    else:
                        snippets.append(content[:280])
                except (ValueError, AttributeError):
                    snippets.append(content[:280])
            if snippets:
                joined = " ".join(s.strip().rstrip(".") + "." for s in snippets if s.strip())
                return f"Based on the knowledge base: {joined} [1]"
            return "I looked into our resources but found nothing relevant. [1]"

        system = next((m.content for m in request.messages if m.role == "system"), "") or ""
        mock_match = _MOCK_REPLY.search(system)
        if mock_match:
            return mock_match.group(1).strip()

        last_user = next(
            (m.content for m in reversed(request.messages) if m.role == "user" and m.content),
            None,
        )
        if last_user:
            cleaned = _TOOL_DIRECTIVE.sub("", last_user).strip()
            if cleaned:
                return (
                    f"Thanks for reaching out! Regarding “{cleaned[:160]}” — "
                    "a teammate or I will help you right away."
                )
        return "Hello! How can I help you today?"

    async def generate(self, request: ChatRequest) -> ChatResult:
        directive = self._pending_directive(request)
        prompt_tokens = sum(_estimate_tokens(m.content or "") for m in request.messages)
        if directive is not None:
            return ChatResult(
                content=None,
                tool_calls=[directive],
                finish_reason="tool_calls",
                usage=Usage(input_tokens=prompt_tokens, output_tokens=8),
            )
        text = self._compose_text(request)
        if request.json_mode:
            text = json.dumps({"response": text})
        return ChatResult(
            content=text,
            tool_calls=[],
            finish_reason="stop",
            usage=Usage(input_tokens=prompt_tokens, output_tokens=_estimate_tokens(text)),
        )

    async def _stream_impl(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        result = await self.generate(request)
        if result.tool_calls:
            call = result.tool_calls[0]
            yield ToolCallStart(id=call.id, name=call.name)
            yield ToolCallEnd(tool_call=call)
            yield StreamFinish(finish_reason="tool_calls", usage=result.usage)
            return
        words = (result.content or "").split(" ")
        for index, word in enumerate(words):
            yield TextDelta(text=word if index == 0 else f" {word}")
        yield StreamFinish(finish_reason="stop", usage=result.usage)

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        return self._stream_impl(request)


class LocalHashEmbedder:
    """Deterministic feature-hashing embedder (offline "embedding model").

    Token unigrams + bigrams hashed into `dim` signed buckets, L2-normalized.
    Cosine similarity then reflects lexical overlap — plenty for dev/tests and a
    sane fallback until a real embedding provider is configured.
    """

    def __init__(self, dim: int | None = None):
        self.dim = dim or get_settings().embedding_dim

    def _tokens(self, text: str) -> list[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        return words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in self._tokens(text):
            digest = hashlib.md5(token.encode()).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]
        return vector

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]
