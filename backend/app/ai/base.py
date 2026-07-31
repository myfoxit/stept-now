"""Provider-agnostic chat/embedding interfaces (design informed by Vercel AI SDK's
LanguageModel spec — see docs/research/vercel-ai.md).

Adapters normalize each vendor's API to `ChatProvider`; everything above
(agent engine, copilot, RAG answerer) only ever sees these types.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal["stop", "tool_calls", "length", "content_filter", "error"]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema (object)


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    raw_input: str | None = None  # unparsed args for repair flows


@dataclass
class ChatMessage:
    """One provider-facing message.

    - system/user: `content`
    - assistant: `content` and/or `tool_calls`
    - tool: `tool_call_id` + `content` (stringified result) + `is_error`
    """

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    is_error: bool = False

    @classmethod
    def system(cls, content: str) -> ChatMessage:
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> ChatMessage:
        return cls(role="user", content=content)

    @classmethod
    def assistant(
        cls, content: str | None, tool_calls: list[ToolCall] | None = None
    ) -> ChatMessage:
        return cls(role="assistant", content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str, *, is_error: bool = False) -> ChatMessage:
        return cls(role="tool", content=content, tool_call_id=tool_call_id, is_error=is_error)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


# --- streaming events -------------------------------------------------------


@dataclass
class TextDelta:
    text: str
    type: Literal["text_delta"] = "text_delta"


@dataclass
class ToolCallStart:
    id: str
    name: str
    type: Literal["tool_call_start"] = "tool_call_start"


@dataclass
class ToolCallDelta:
    id: str
    args_delta: str
    type: Literal["tool_call_delta"] = "tool_call_delta"


@dataclass
class ToolCallEnd:
    tool_call: ToolCall
    type: Literal["tool_call_end"] = "tool_call_end"


@dataclass
class StreamFinish:
    finish_reason: FinishReason
    usage: Usage
    type: Literal["finish"] = "finish"


@dataclass
class StreamError:
    message: str
    retryable: bool = False
    type: Literal["error"] = "error"


StreamEvent = TextDelta | ToolCallStart | ToolCallDelta | ToolCallEnd | StreamFinish | StreamError


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: FinishReason
    usage: Usage


@dataclass
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    tools: list[ToolSpec] = field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None
    json_mode: bool = False


class ProviderError(Exception):
    """Normalized provider failure."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


class ChatProvider(Protocol):
    async def generate(self, request: ChatRequest) -> ChatResult: ...

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]: ...


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
