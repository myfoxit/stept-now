"""Google Gemini adapter: complete function calls, finish inference, streaming."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.ai.base import (
    ChatMessage,
    ChatRequest,
    ProviderError,
    StreamFinish,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    ToolCallStart,
    ToolSpec,
)
from app.ai.providers.google import GoogleProvider
from tests.ai.utils import collect, data_line, sse_response

BASE = "https://generativelanguage.googleapis.com/v1beta"
GENERATE_URL = f"{BASE}/models/gemini-2.5-flash:generateContent"
STREAM_URL = f"{BASE}/models/gemini-2.5-flash:streamGenerateContent"

SEARCH_TOOL = ToolSpec(
    name="search",
    description="Search the knowledge base",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
)


def make_request(**overrides):
    defaults: dict = {"model": "gemini-2.5-flash", "messages": [ChatMessage.user("hi")]}
    defaults.update(overrides)
    return ChatRequest(**defaults)


@respx.mock
async def test_generate_text_and_request_shape():
    route = respx.post(GENERATE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "Hello"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 6, "candidatesTokenCount": 2},
            },
        )
    )
    provider = GoogleProvider(api_key="g-key")
    result = await provider.generate(
        make_request(
            messages=[ChatMessage.system("be brief"), ChatMessage.user("hi")],
            temperature=0.2,
            max_tokens=32,
            json_mode=True,
        )
    )
    assert result.content == "Hello"
    assert result.finish_reason == "stop"
    assert result.usage.input_tokens == 6
    assert result.usage.output_tokens == 2

    request = route.calls.last.request
    assert request.headers["x-goog-api-key"] == "g-key"
    sent = json.loads(request.content)
    assert sent["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert sent["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    assert sent["generationConfig"] == {
        "temperature": 0.2,
        "maxOutputTokens": 32,
        "responseMimeType": "application/json",
    }


@respx.mock
async def test_generate_complete_function_call_generates_id():
    respx.post(GENERATE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"functionCall": {"name": "search", "args": {"query": "refunds"}}}
                            ],
                        },
                        # Google reports STOP even for tool calls — must infer tool_calls.
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 4},
            },
        )
    )
    provider = GoogleProvider(api_key="g-key")
    result = await provider.generate(make_request(tools=[SEARCH_TOOL]))
    assert result.finish_reason == "tool_calls"
    call = result.tool_calls[0]
    assert call.name == "search"
    assert call.input == {"query": "refunds"}  # args arrive complete, already parsed
    assert call.id  # no id on the wire → synthesized
    assert json.loads(call.raw_input) == {"query": "refunds"}


@respx.mock
async def test_function_response_roundtrip_uses_call_name():
    route = respx.post(GENERATE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "done"}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )
    )
    provider = GoogleProvider(api_key="g-key")
    call = ToolCall(id="call_gen_1", name="search", input={"query": "refunds"})
    await provider.generate(
        make_request(
            messages=[
                ChatMessage.user("How do refunds work?"),
                ChatMessage.assistant(None, [call]),
                ChatMessage.tool_result("call_gen_1", "plain text result"),
            ],
            tools=[SEARCH_TOOL],
        )
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "search",
                    "description": "Search the knowledge base",
                    "parameters": SEARCH_TOOL.input_schema,
                }
            ]
        }
    ]
    model_turn = sent["contents"][1]
    assert model_turn["role"] == "model"
    assert model_turn["parts"] == [
        {"functionCall": {"name": "search", "args": {"query": "refunds"}}}
    ]
    tool_turn = sent["contents"][2]
    assert tool_turn["role"] == "user"
    # name recovered from the assistant call id; non-JSON content wrapped as object
    assert tool_turn["parts"] == [
        {"functionResponse": {"name": "search", "response": {"result": "plain text result"}}}
    ]


@pytest.mark.parametrize(
    ("finish_reason", "expected"),
    [("MAX_TOKENS", "length"), ("SAFETY", "content_filter"), ("MALFORMED_FUNCTION_CALL", "error")],
)
@respx.mock
async def test_generate_finish_reason_mapping(finish_reason, expected):
    respx.post(GENERATE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "x"}]},
                        "finishReason": finish_reason,
                    }
                ]
            },
        )
    )
    provider = GoogleProvider(api_key="g-key")
    result = await provider.generate(make_request())
    assert result.finish_reason == expected


@respx.mock
async def test_stream_text_chunks():
    body = data_line(
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Hel"}]}}]}
    ) + data_line(
        {
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": "lo"}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
        }
    )
    respx.post(STREAM_URL).mock(return_value=sse_response(body, chunk_size=15))
    provider = GoogleProvider(api_key="g-key")
    events = await collect(provider.stream(make_request()))

    texts = [e.text for e in events if isinstance(e, TextDelta)]
    assert texts == ["Hel", "lo"]
    finish = events[-1]
    assert isinstance(finish, StreamFinish)
    assert finish.finish_reason == "stop"
    assert finish.usage.input_tokens == 7
    assert finish.usage.output_tokens == 3


@respx.mock
async def test_stream_complete_function_call_no_arg_deltas():
    body = data_line(
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"functionCall": {"name": "search", "args": {"query": "x"}}}],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2},
        }
    )
    respx.post(STREAM_URL).mock(return_value=sse_response(body))
    provider = GoogleProvider(api_key="g-key")
    events = await collect(provider.stream(make_request(tools=[SEARCH_TOOL])))

    starts = [e for e in events if isinstance(e, ToolCallStart)]
    ends = [e for e in events if isinstance(e, ToolCallEnd)]
    assert len(starts) == 1 and len(ends) == 1
    assert starts[0].name == "search"
    assert ends[0].tool_call.input == {"query": "x"}
    assert ends[0].tool_call.id == starts[0].id
    finish = events[-1]
    assert isinstance(finish, StreamFinish)
    assert finish.finish_reason == "tool_calls"  # STOP + calls present → tool_calls


@pytest.mark.parametrize(("status", "retryable"), [(400, False), (429, True), (503, True)])
@respx.mock
async def test_error_mapping(status, retryable):
    respx.post(GENERATE_URL).mock(
        return_value=httpx.Response(
            status, json={"error": {"code": status, "message": "bad", "status": "X"}}
        )
    )
    provider = GoogleProvider(api_key="g-secret")
    with pytest.raises(ProviderError) as err:
        await provider.generate(make_request())
    assert err.value.status_code == status
    assert err.value.retryable is retryable
    assert "g-secret" not in str(err.value)
