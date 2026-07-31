"""Google Gemini API adapter (generateContent / streamGenerateContent?alt=sse).

Wire quirks handled here (docs/research/vercel-ai.md §5):
- function calls arrive COMPLETE (args already an object, usually no id) →
  synthesize a call id and emit start+end together, never arg deltas;
- tool results go back as `functionResponse` parts on a user turn, keyed by
  function *name* (recovered from the originating assistant tool_call id);
- `finishReason: STOP` with function calls present still means tool_calls;
- usage in `usageMetadata` {promptTokenCount, candidatesTokenCount}.
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
)
from app.core.db import uuid7

DEFAULT_GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

_FILTER_REASONS = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
)


def _map_finish(raw: str | None, has_tool_calls: bool) -> FinishReason:
    # Google reports STOP even when the model called tools — infer tool_calls.
    if has_tool_calls and raw in (None, "STOP"):
        return "tool_calls"
    if raw == "MAX_TOKENS":
        return "length"
    if raw == "MALFORMED_FUNCTION_CALL":
        return "error"
    if raw in _FILTER_REASONS:
        return "content_filter"
    return "tool_calls" if has_tool_calls else "stop"


def _usage(data: dict[str, Any] | None) -> Usage:
    data = data or {}
    return Usage(
        input_tokens=int(data.get("promptTokenCount") or 0),
        output_tokens=int(data.get("candidatesTokenCount") or 0),
    )


def _new_call_id() -> str:
    return f"call_{uuid7()}"


class GoogleProvider:
    """ChatProvider for the Gemini REST API."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_GOOGLE_BASE_URL):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.label = "google"

    # -- request building ---------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        return headers

    def _payload(self, request: ChatRequest) -> dict[str, Any]:
        system_texts = [m.content for m in request.messages if m.role == "system" and m.content]
        # functionResponse parts are keyed by function *name*; recover it from
        # the assistant tool_call that carries the matching id.
        call_names = {
            call.id: call.name for m in request.messages for call in m.tool_calls if call.id
        }

        contents: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "system":
                continue
            if message.role == "user":
                contents.append({"role": "user", "parts": [{"text": message.content or ""}]})
            elif message.role == "assistant":
                parts: list[dict[str, Any]] = []
                if message.content:
                    parts.append({"text": message.content})
                for call in message.tool_calls:
                    parts.append({"functionCall": {"name": call.name, "args": call.input}})
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            else:  # tool result → user turn functionResponse part (merge consecutive)
                name = call_names.get(message.tool_call_id or "", "tool")
                response_obj = _response_object(message.content, is_error=message.is_error)
                part = {"functionResponse": {"name": name, "response": response_obj}}
                previous = contents[-1] if contents else None
                if (
                    previous is not None
                    and previous["role"] == "user"
                    and previous["parts"]
                    and "functionResponse" in previous["parts"][0]
                ):
                    previous["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})

        payload: dict[str, Any] = {"contents": contents}
        if system_texts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.input_schema,
                        }
                        for tool in request.tools
                    ]
                }
            ]
        generation_config: dict[str, Any] = {}
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.max_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_tokens
        if request.json_mode:
            generation_config["responseMimeType"] = "application/json"
        if generation_config:
            payload["generationConfig"] = generation_config
        return payload

    # -- generate -----------------------------------------------------------

    async def generate(self, request: ChatRequest) -> ChatResult:
        try:
            async with http_client() as client:
                response = await client.post(
                    f"{self.base_url}/models/{request.model}:generateContent",
                    json=self._payload(request),
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise network_error(self.label, exc) from exc
        if response.status_code >= 400:
            raise error_from_status(self.label, response.status_code, response.text)

        data = response.json()
        candidates = data.get("candidates") or [{}]
        candidate = candidates[0]
        texts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in (candidate.get("content") or {}).get("parts") or []:
            if part.get("thought"):
                continue
            if part.get("text"):
                texts.append(part["text"])
            elif part.get("functionCall"):
                function_call = part["functionCall"]
                args = function_call.get("args") or {}
                tool_calls.append(
                    ToolCall(
                        id=function_call.get("id") or _new_call_id(),
                        name=function_call.get("name") or "",
                        input=args if isinstance(args, dict) else {},
                        raw_input=json.dumps(args),
                    )
                )
        return ChatResult(
            content="".join(texts) or None,
            tool_calls=tool_calls,
            finish_reason=_map_finish(candidate.get("finishReason"), bool(tool_calls)),
            usage=_usage(data.get("usageMetadata")),
        )

    # -- stream -------------------------------------------------------------

    async def _stream_impl(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        finish_raw: str | None = None
        usage = Usage()
        saw_tool_call = False

        try:
            async with (
                http_client() as client,
                client.stream(
                    "POST",
                    f"{self.base_url}/models/{request.model}:streamGenerateContent",
                    params={"alt": "sse"},
                    json=self._payload(request),
                    headers=self._headers(),
                ) as response,
            ):
                if response.status_code >= 400:
                    body = await response.aread()
                    raise error_from_status(self.label, response.status_code, body)

                async for _event, data in iter_sse(response):
                    chunk = parse_json_object(data)
                    if chunk is None:
                        continue
                    if chunk.get("usageMetadata"):
                        usage = _usage(chunk["usageMetadata"])
                    for candidate in chunk.get("candidates") or []:
                        for part in (candidate.get("content") or {}).get("parts") or []:
                            if part.get("thought"):
                                continue
                            if part.get("text"):
                                yield TextDelta(text=part["text"])
                            elif part.get("functionCall"):
                                function_call = part["functionCall"]
                                args = function_call.get("args") or {}
                                call = ToolCall(
                                    id=function_call.get("id") or _new_call_id(),
                                    name=function_call.get("name") or "",
                                    input=args if isinstance(args, dict) else {},
                                    raw_input=json.dumps(args),
                                )
                                saw_tool_call = True
                                # Args arrive complete — no delta phase.
                                yield ToolCallStart(id=call.id, name=call.name)
                                yield ToolCallEnd(tool_call=call)
                        if candidate.get("finishReason"):
                            finish_raw = candidate["finishReason"]
        except httpx.HTTPError as exc:
            raise network_error(self.label, exc) from exc

        yield StreamFinish(finish_reason=_map_finish(finish_raw, saw_tool_call), usage=usage)

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamEvent]:
        return self._stream_impl(request)


def _response_object(content: str | None, *, is_error: bool) -> dict[str, Any]:
    """Gemini functionResponse.response must be an object."""
    if content:
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    key = "error" if is_error else "result"
    return {key: content or ""}
