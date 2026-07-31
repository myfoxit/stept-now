"""Anthropic Messages API adapter.

Wire quirks handled here (docs/research/vercel-ai.md §5):
- system prompt is a top-level `system` field, `max_tokens` is required;
- tool results are user messages containing `tool_result` content blocks
  (consecutive results merge into one user turn);
- streaming is typed SSE events: content_block_start (tool_use carries id+name,
  may already carry full input) → input_json_delta partial JSON → block stop;
  usage splits across message_start (input) and message_delta (output);
- HTTP 529 / in-stream `overloaded_error` → retryable ProviderError.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.ai.base import (
    ChatRequest,
    ChatResult,
    FinishReason,
    ProviderError,
    StreamEvent,
    StreamFinish,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    Usage,
)
from app.ai.providers import (
    error_from_status,
    http_client,
    iter_sse,
    network_error,
    parse_json_object,
    parse_tool_args,
)

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096

_STOP_MAP: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "refusal": "content_filter",
}


def _map_stop(raw: str | None, has_tool_calls: bool) -> FinishReason:
    if raw is not None and raw in _STOP_MAP:
        return _STOP_MAP[raw]
    return "tool_calls" if has_tool_calls else "stop"


class AnthropicProvider:
    """ChatProvider for the Anthropic Messages API."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_ANTHROPIC_BASE_URL):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.label = "anthropic"

    # -- request building ---------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

    def _payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        system_parts = [m.content for m in request.messages if m.role == "system" and m.content]
        if request.json_mode:
            system_parts.append("Respond with a single valid JSON object and nothing else.")

        messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "system":
                continue
            if message.role == "user":
                messages.append({"role": "user", "content": message.content or ""})
            elif message.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    blocks.append(
                        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
                    )
                messages.append(
                    {"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]}
                )
            else:  # tool result → user message with tool_result blocks (merge consecutive)
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id or "",
                    "content": message.content or "",
                }
                if message.is_error:
                    block["is_error"] = True
                previous = messages[-1] if messages else None
                if (
                    previous is not None
                    and previous["role"] == "user"
                    and isinstance(previous["content"], list)
                    and previous["content"]
                    and previous["content"][0].get("type") == "tool_result"
                ):
                    previous["content"].append(block)
                else:
                    messages.append({"role": "user", "content": [block]})

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.tools
            ]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if stream:
            payload["stream"] = True
        return payload

    # -- generate -----------------------------------------------------------

    async def generate(self, request: ChatRequest) -> ChatResult:
        try:
            async with http_client() as client:
                response = await client.post(
                    f"{self.base_url}/messages",
                    json=self._payload(request, stream=False),
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise network_error(self.label, exc) from exc
        if response.status_code >= 400:
            raise error_from_status(self.label, response.status_code, response.text)

        data = response.json()
        texts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                texts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                raw_input = block.get("input")
                tool_calls.append(
                    ToolCall(
                        id=block.get("id") or f"toolu_{len(tool_calls)}",
                        name=block.get("name") or "",
                        input=raw_input if isinstance(raw_input, dict) else {},
                        raw_input=json.dumps(raw_input) if raw_input is not None else None,
                    )
                )
        usage_data = data.get("usage") or {}
        return ChatResult(
            content="".join(texts) or None,
            tool_calls=tool_calls,
            finish_reason=_map_stop(data.get("stop_reason"), bool(tool_calls)),
            usage=Usage(
                input_tokens=int(usage_data.get("input_tokens") or 0),
                output_tokens=int(usage_data.get("output_tokens") or 0),
            ),
        )

    # -- stream -------------------------------------------------------------

    async def _stream_impl(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        blocks: dict[int, dict[str, Any]] = {}  # index → pending tool_use block
        input_tokens = 0
        output_tokens = 0
        stop_reason: str | None = None
        saw_tool_call = False

        try:
            async with (
                http_client() as client,
                client.stream(
                    "POST",
                    f"{self.base_url}/messages",
                    json=self._payload(request, stream=True),
                    headers=self._headers(),
                ) as response,
            ):
                if response.status_code >= 400:
                    body = await response.aread()
                    raise error_from_status(self.label, response.status_code, body)

                async for event_name, data in iter_sse(response):
                    chunk = parse_json_object(data)
                    if chunk is None:
                        continue
                    event_type = chunk.get("type") or event_name

                    if event_type == "message_start":
                        message = chunk.get("message") or {}
                        input_tokens = int((message.get("usage") or {}).get("input_tokens") or 0)
                    elif event_type == "content_block_start":
                        block = chunk.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            index = int(chunk.get("index") or 0)
                            state: dict[str, Any] = {
                                "id": block.get("id") or f"toolu_{index}",
                                "name": block.get("name") or "",
                                "args": "",
                            }
                            # The start event may already carry the complete input.
                            if block.get("input"):
                                state["args"] = json.dumps(block["input"])
                            blocks[index] = state
                            yield ToolCallStart(id=state["id"], name=state["name"])
                    elif event_type == "content_block_delta":
                        delta = chunk.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            if delta.get("text"):
                                yield TextDelta(text=delta["text"])
                        elif delta.get("type") == "input_json_delta":
                            state_or_none = blocks.get(int(chunk.get("index") or 0))
                            partial = delta.get("partial_json")
                            if state_or_none is not None and partial:
                                state_or_none["args"] += partial
                                yield ToolCallDelta(id=state_or_none["id"], args_delta=partial)
                    elif event_type == "content_block_stop":
                        finished = blocks.pop(int(chunk.get("index") or 0), None)
                        if finished is not None:
                            saw_tool_call = True
                            yield ToolCallEnd(
                                tool_call=ToolCall(
                                    id=finished["id"],
                                    name=finished["name"],
                                    input=parse_tool_args(finished["args"]),
                                    raw_input=finished["args"] or None,
                                )
                            )
                    elif event_type == "message_delta":
                        delta_info = chunk.get("delta") or {}
                        if delta_info.get("stop_reason"):
                            stop_reason = delta_info["stop_reason"]
                        usage_data = chunk.get("usage") or {}
                        if usage_data.get("output_tokens") is not None:
                            output_tokens = int(usage_data["output_tokens"])
                    elif event_type == "message_stop":
                        break
                    elif event_type == "error":
                        error = chunk.get("error") or {}
                        error_type = error.get("type") or "api_error"
                        overloaded = error_type == "overloaded_error"
                        raise ProviderError(
                            f"anthropic: {error_type}: {_truncate(error.get('message'))}",
                            status_code=529 if overloaded else None,
                            retryable=overloaded or error_type == "api_error",
                        )
                    # ping and unknown events are ignored
        except httpx.HTTPError as exc:
            raise network_error(self.label, exc) from exc

        yield StreamFinish(
            finish_reason=_map_stop(stop_reason, saw_tool_call),
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        return self._stream_impl(request)


def _truncate(message: Any) -> str:
    return str(message or "")[:300]
