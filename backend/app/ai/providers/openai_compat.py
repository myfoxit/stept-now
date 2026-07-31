"""OpenAI Chat Completions adapter — also serves openai_compatible and ollama.

Wire quirks handled here (docs/research/vercel-ai.md §5):
- streaming tool calls arrive as index-keyed fragments (id+name only in the
  first fragment) → accumulate per index, finalize on flush;
- usage arrives only in the final stream chunk (requires
  `stream_options.include_usage`);
- json_mode → `response_format: {"type": "json_object"}`.
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

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"

_FINISH_MAP: dict[str, FinishReason] = {
    "stop": "stop",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "length": "length",
    "content_filter": "content_filter",
}


def _map_finish(raw: str | None, has_tool_calls: bool) -> FinishReason:
    if has_tool_calls:
        return "tool_calls"
    return _FINISH_MAP.get(raw or "", "stop")


def _usage(data: dict[str, Any] | None) -> Usage:
    data = data or {}
    return Usage(
        input_tokens=int(data.get("prompt_tokens") or 0),
        output_tokens=int(data.get("completion_tokens") or 0),
    )


class OpenAICompatProvider:
    """ChatProvider for any Chat-Completions-shaped API (OpenAI, Ollama, vLLM, …)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        label: str = "openai",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.label = label

    # -- request building ---------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "tool":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id or "",
                        "content": message.content or "",
                    }
                )
            elif message.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": message.content}
                if message.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.raw_input or json.dumps(call.input),
                            },
                        }
                        for call in message.tool_calls
                    ]
                messages.append(entry)
            else:  # system / user
                messages.append({"role": message.role, "content": message.content or ""})

        payload: dict[str, Any] = {"model": request.model, "messages": messages}
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    # -- generate -----------------------------------------------------------

    async def generate(self, request: ChatRequest) -> ChatResult:
        try:
            async with http_client() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=self._payload(request, stream=False),
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise network_error(self.label, exc) from exc
        if response.status_code >= 400:
            raise error_from_status(self.label, response.status_code, response.text)

        data = response.json()
        choices = data.get("choices") or [{}]
        message = choices[0].get("message") or {}
        tool_calls: list[ToolCall] = []
        for index, item in enumerate(message.get("tool_calls") or []):
            function = item.get("function") or {}
            raw_args = function.get("arguments") or ""
            tool_calls.append(
                ToolCall(
                    id=item.get("id") or f"call_{index}",
                    name=function.get("name") or "",
                    input=parse_tool_args(raw_args),
                    raw_input=raw_args or None,
                )
            )
        return ChatResult(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=_map_finish(choices[0].get("finish_reason"), bool(tool_calls)),
            usage=_usage(data.get("usage")),
        )

    # -- stream -------------------------------------------------------------

    async def _stream_impl(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        # Index-keyed tool-call fragment tracker (id+name arrive only in the
        # first fragment; later fragments carry only `index` + argument chunks).
        calls: dict[int, dict[str, Any]] = {}
        order: list[int] = []
        finish_raw: str | None = None
        usage = Usage()

        try:
            async with (
                http_client() as client,
                client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=self._payload(request, stream=True),
                    headers=self._headers(),
                ) as response,
            ):
                if response.status_code >= 400:
                    body = await response.aread()
                    raise error_from_status(self.label, response.status_code, body)

                async for _event, data in iter_sse(response):
                    if data.strip() == "[DONE]":
                        break
                    chunk = parse_json_object(data)
                    if chunk is None:
                        continue
                    if chunk.get("usage"):
                        usage = _usage(chunk["usage"])
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield TextDelta(text=delta["content"])
                        for fragment in delta.get("tool_calls") or []:
                            index = int(fragment.get("index") or 0)
                            state = calls.get(index)
                            if state is None:
                                state = {"id": "", "name": "", "args": "", "started": False}
                                calls[index] = state
                                order.append(index)
                            if fragment.get("id"):
                                state["id"] = fragment["id"]
                            function = fragment.get("function") or {}
                            if function.get("name"):
                                state["name"] += function["name"]
                            if not state["started"] and state["name"]:
                                state["started"] = True
                                state["id"] = state["id"] or f"call_{index}"
                                yield ToolCallStart(id=state["id"], name=state["name"])
                            if function.get("arguments"):
                                state["args"] += function["arguments"]
                                yield ToolCallDelta(
                                    id=state["id"] or f"call_{index}",
                                    args_delta=function["arguments"],
                                )
                        if choice.get("finish_reason"):
                            finish_raw = choice["finish_reason"]
        except httpx.HTTPError as exc:
            raise network_error(self.label, exc) from exc

        for index in order:
            state = calls[index]
            yield ToolCallEnd(
                tool_call=ToolCall(
                    id=state["id"] or f"call_{index}",
                    name=state["name"],
                    input=parse_tool_args(state["args"]),
                    raw_input=state["args"] or None,
                )
            )
        yield StreamFinish(finish_reason=_map_finish(finish_raw, bool(order)), usage=usage)

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        return self._stream_impl(request)

    # -- embeddings ---------------------------------------------------------

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        try:
            async with http_client() as client:
                response = await client.post(
                    f"{self.base_url}/embeddings",
                    json={"model": model, "input": texts},
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise network_error(self.label, exc) from exc
        if response.status_code >= 400:
            raise error_from_status(self.label, response.status_code, response.text)
        items = response.json().get("data") or []
        ordered = sorted(items, key=lambda item: item.get("index", 0))
        return [[float(v) for v in item.get("embedding") or []] for item in ordered]
