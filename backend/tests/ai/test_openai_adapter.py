"""OpenAI(-compatible) adapter: wire format, streaming fragments, error mapping."""

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
    ToolCallDelta,
    ToolCallEnd,
    ToolCallStart,
    ToolSpec,
)
from app.ai.providers.openai_compat import OpenAICompatProvider
from tests.ai.utils import collect, data_line, sse_response

CHAT_URL = "https://api.openai.com/v1/chat/completions"
EMBED_URL = "https://api.openai.com/v1/embeddings"

SEARCH_TOOL = ToolSpec(
    name="search",
    description="Search the knowledge base",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
)


def make_request(**overrides):
    defaults: dict = {"model": "gpt-4o", "messages": [ChatMessage.user("hi")]}
    defaults.update(overrides)
    return ChatRequest(**defaults)


@respx.mock
async def test_generate_text_and_request_shape():
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 3},
            },
        )
    )
    provider = OpenAICompatProvider(api_key="sk-test-123")
    result = await provider.generate(
        make_request(
            messages=[ChatMessage.system("be brief"), ChatMessage.user("hi")],
            temperature=0.5,
            max_tokens=64,
            json_mode=True,
        )
    )
    assert result.content == "Hello!"
    assert result.finish_reason == "stop"
    assert result.usage.input_tokens == 9
    assert result.usage.output_tokens == 3

    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-test-123"
    sent = json.loads(request.content)
    assert sent["model"] == "gpt-4o"
    assert sent["messages"][0] == {"role": "system", "content": "be brief"}
    assert sent["temperature"] == 0.5
    assert sent["max_tokens"] == 64
    assert sent["response_format"] == {"type": "json_object"}
    assert "stream" not in sent


@respx.mock
async def test_generate_tool_call_parse():
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "search",
                                        "arguments": '{"query": "refunds"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )
    )
    provider = OpenAICompatProvider(api_key="sk-x")
    result = await provider.generate(make_request(tools=[SEARCH_TOOL]))
    assert result.finish_reason == "tool_calls"
    assert result.content is None
    call = result.tool_calls[0]
    assert call.id == "call_abc"
    assert call.name == "search"
    assert call.input == {"query": "refunds"}
    assert call.raw_input == '{"query": "refunds"}'

    sent = json.loads(route.calls.last.request.content)
    assert sent["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search the knowledge base",
                "parameters": SEARCH_TOOL.input_schema,
            },
        }
    ]


@respx.mock
async def test_tool_result_roundtrip_shape():
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"index": 0, "message": {"content": "done"}, "finish_reason": "stop"}]
            },
        )
    )
    provider = OpenAICompatProvider(api_key="sk-x")
    call = ToolCall(id="call_1", name="search", input={"query": "refunds"})
    await provider.generate(
        make_request(
            messages=[
                ChatMessage.user("How do refunds work?"),
                ChatMessage.assistant(None, [call]),
                ChatMessage.tool_result("call_1", '{"results": []}'),
            ]
        )
    )
    sent = json.loads(route.calls.last.request.content)
    assistant = sent["messages"][1]
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["type"] == "function"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"query": "refunds"}
    assert sent["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"results": []}',
    }


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(401, False), (403, False), (404, False), (429, True), (500, True), (503, True)],
)
@respx.mock
async def test_error_mapping(status, retryable):
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            status, json={"error": {"message": "nope", "type": "invalid_request_error"}}
        )
    )
    provider = OpenAICompatProvider(api_key="sk-x")
    with pytest.raises(ProviderError) as err:
        await provider.generate(make_request())
    assert err.value.status_code == status
    assert err.value.retryable is retryable
    assert "nope" in str(err.value)
    assert "sk-x" not in str(err.value)


@respx.mock
async def test_network_error_is_retryable():
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    provider = OpenAICompatProvider(api_key="sk-x")
    with pytest.raises(ProviderError) as err:
        await provider.generate(make_request())
    assert err.value.retryable is True
    assert err.value.status_code is None


@respx.mock
async def test_stream_text_with_split_chunk_boundaries():
    body = (
        data_line({"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hel"}}]})
        + data_line({"choices": [{"index": 0, "delta": {"content": "lo!"}}]})
        + data_line({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        + data_line({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
        + "data: [DONE]\n\n"
    )
    # chunk_size=7 splits every JSON line across many transport chunks
    respx.post(CHAT_URL).mock(return_value=sse_response(body, chunk_size=7))
    provider = OpenAICompatProvider(api_key="sk-x")
    events = await collect(provider.stream(make_request()))

    texts = [e.text for e in events if isinstance(e, TextDelta)]
    assert texts == ["Hel", "lo!"]
    finish = events[-1]
    assert isinstance(finish, StreamFinish)
    assert finish.finish_reason == "stop"
    assert finish.usage.input_tokens == 5
    assert finish.usage.output_tokens == 2


@respx.mock
async def test_stream_tool_call_index_keyed_fragments():
    # id+name only in the first fragment; later fragments carry index + arg chunks.
    body = (
        data_line(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "search", "arguments": ""},
                                }
                            ]
                        },
                    }
                ]
            }
        )
        + data_line(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": '{"query": "ref'}}
                            ]
                        },
                    }
                ]
            }
        )
        + data_line(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": 'unds"}'}}]
                        },
                    }
                ]
            }
        )
        + data_line({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        + "data: [DONE]\n\n"
    )
    respx.post(CHAT_URL).mock(return_value=sse_response(body, chunk_size=11))
    provider = OpenAICompatProvider(api_key="sk-x")
    events = await collect(provider.stream(make_request(tools=[SEARCH_TOOL])))

    starts = [e for e in events if isinstance(e, ToolCallStart)]
    deltas = [e for e in events if isinstance(e, ToolCallDelta)]
    ends = [e for e in events if isinstance(e, ToolCallEnd)]
    assert [(s.id, s.name) for s in starts] == [("call_1", "search")]
    assert "".join(d.args_delta for d in deltas) == '{"query": "refunds"}'
    assert len(ends) == 1
    assert ends[0].tool_call.input == {"query": "refunds"}
    assert ends[0].tool_call.raw_input == '{"query": "refunds"}'
    finish = events[-1]
    assert isinstance(finish, StreamFinish)
    assert finish.finish_reason == "tool_calls"


@respx.mock
async def test_stream_http_error_raises():
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(429, json={"error": {"message": "rate limited"}})
    )
    provider = OpenAICompatProvider(api_key="sk-x")
    with pytest.raises(ProviderError) as err:
        await collect(provider.stream(make_request()))
    assert err.value.status_code == 429
    assert err.value.retryable is True


@respx.mock
async def test_embed_orders_by_index():
    route = respx.post(EMBED_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.3, 0.4]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ],
                "usage": {"prompt_tokens": 4},
            },
        )
    )
    provider = OpenAICompatProvider(api_key="sk-x")
    vectors = await provider.embed(["a", "b"], model="text-embedding-3-small")
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"model": "text-embedding-3-small", "input": ["a", "b"]}


@respx.mock
async def test_custom_base_url_without_key_skips_auth_header():
    route = respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"content": "ok"}, "finish_reason": "stop"}]},
        )
    )
    provider = OpenAICompatProvider(
        api_key="", base_url="http://localhost:11434/v1/", label="ollama"
    )
    result = await provider.generate(make_request(model="llama3"))
    assert result.content == "ok"
    assert "authorization" not in route.calls.last.request.headers
