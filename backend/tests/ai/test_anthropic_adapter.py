"""Anthropic adapter: Messages wire format, block streaming, error mapping."""

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
from app.ai.providers.anthropic import ANTHROPIC_VERSION, AnthropicProvider
from tests.ai.utils import collect, event_line, sse_response

MESSAGES_URL = "https://api.anthropic.com/v1/messages"

SEARCH_TOOL = ToolSpec(
    name="search",
    description="Search the knowledge base",
    input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
)


def make_request(**overrides):
    defaults: dict = {"model": "claude-haiku-4-5", "messages": [ChatMessage.user("hi")]}
    defaults.update(overrides)
    return ChatRequest(**defaults)


@respx.mock
async def test_generate_text_and_request_shape():
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Hi there"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
        )
    )
    provider = AnthropicProvider(api_key="sk-ant-test")
    result = await provider.generate(
        make_request(messages=[ChatMessage.system("be brief"), ChatMessage.user("hi")])
    )
    assert result.content == "Hi there"
    assert result.finish_reason == "stop"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 4

    request = route.calls.last.request
    assert request.headers["x-api-key"] == "sk-ant-test"
    assert request.headers["anthropic-version"] == ANTHROPIC_VERSION
    sent = json.loads(request.content)
    assert sent["system"] == "be brief"  # top-level, not a message
    assert sent["max_tokens"] == 4096  # required, defaulted
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


@respx.mock
async def test_generate_tool_use_parse():
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "Let me look."},
                    {
                        "type": "tool_use",
                        "id": "toolu_01",
                        "name": "search",
                        "input": {"query": "refunds"},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 20, "output_tokens": 9},
            },
        )
    )
    provider = AnthropicProvider(api_key="k")
    result = await provider.generate(make_request(tools=[SEARCH_TOOL]))
    assert result.finish_reason == "tool_calls"
    assert result.content == "Let me look."
    call = result.tool_calls[0]
    assert (call.id, call.name, call.input) == ("toolu_01", "search", {"query": "refunds"})
    assert json.loads(call.raw_input) == {"query": "refunds"}


@respx.mock
async def test_tool_result_roundtrip_merges_consecutive_results():
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    provider = AnthropicProvider(api_key="k")
    calls = [
        ToolCall(id="toolu_a", name="search", input={"query": "a"}),
        ToolCall(id="toolu_b", name="search", input={"query": "b"}),
    ]
    await provider.generate(
        make_request(
            messages=[
                ChatMessage.user("question"),
                ChatMessage.assistant("Checking.", calls),
                ChatMessage.tool_result("toolu_a", "result a"),
                ChatMessage.tool_result("toolu_b", "oops", is_error=True),
            ],
            tools=[SEARCH_TOOL],
            max_tokens=99,
        )
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["max_tokens"] == 99
    assert sent["tools"] == [
        {
            "name": "search",
            "description": "Search the knowledge base",
            "input_schema": SEARCH_TOOL.input_schema,
        }
    ]
    assistant = sent["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == {"type": "text", "text": "Checking."}
    assert assistant["content"][1] == {
        "type": "tool_use",
        "id": "toolu_a",
        "name": "search",
        "input": {"query": "a"},
    }
    # Both tool results merged into ONE user message with tool_result blocks.
    tool_turn = sent["messages"][2]
    assert tool_turn["role"] == "user"
    assert tool_turn["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_a", "content": "result a"},
        {"type": "tool_result", "tool_use_id": "toolu_b", "content": "oops", "is_error": True},
    ]
    assert len(sent["messages"]) == 3


@respx.mock
async def test_generate_max_tokens_stop_maps_to_length():
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "truncat"}],
                "stop_reason": "max_tokens",
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )
    )
    provider = AnthropicProvider(api_key="k")
    result = await provider.generate(make_request())
    assert result.finish_reason == "length"


@respx.mock
async def test_stream_text_split_usage_and_events():
    body = (
        event_line(
            "message_start",
            {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
        )
        + event_line(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        )
        + event_line("ping", {"type": "ping"})
        + event_line(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hel"},
            },
        )
        + event_line(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "lo"},
            },
        )
        + event_line("content_block_stop", {"type": "content_block_stop", "index": 0})
        + event_line(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 12},
            },
        )
        + event_line("message_stop", {"type": "message_stop"})
    )
    respx.post(MESSAGES_URL).mock(return_value=sse_response(body, chunk_size=9))
    provider = AnthropicProvider(api_key="k")
    events = await collect(provider.stream(make_request()))

    texts = [e.text for e in events if isinstance(e, TextDelta)]
    assert texts == ["Hel", "lo"]
    finish = events[-1]
    assert isinstance(finish, StreamFinish)
    assert finish.finish_reason == "stop"
    # usage split across message_start (input) and message_delta (output)
    assert finish.usage.input_tokens == 10
    assert finish.usage.output_tokens == 12


@respx.mock
async def test_stream_tool_use_input_json_delta_across_chunks():
    body = (
        event_line(
            "message_start",
            {"type": "message_start", "message": {"usage": {"input_tokens": 7}}},
        )
        + event_line(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_9", "name": "search"},
            },
        )
        + event_line(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"query": "ref'},
            },
        )
        + event_line(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": 'unds"}'},
            },
        )
        + event_line("content_block_stop", {"type": "content_block_stop", "index": 0})
        + event_line(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 5},
            },
        )
        + event_line("message_stop", {"type": "message_stop"})
    )
    respx.post(MESSAGES_URL).mock(return_value=sse_response(body, chunk_size=13))
    provider = AnthropicProvider(api_key="k")
    events = await collect(provider.stream(make_request(tools=[SEARCH_TOOL])))

    starts = [e for e in events if isinstance(e, ToolCallStart)]
    deltas = [e for e in events if isinstance(e, ToolCallDelta)]
    ends = [e for e in events if isinstance(e, ToolCallEnd)]
    assert [(s.id, s.name) for s in starts] == [("toolu_9", "search")]
    assert "".join(d.args_delta for d in deltas) == '{"query": "refunds"}'
    assert ends[0].tool_call.input == {"query": "refunds"}
    finish = events[-1]
    assert isinstance(finish, StreamFinish)
    assert finish.finish_reason == "tool_calls"
    assert finish.usage.output_tokens == 5


@respx.mock
async def test_stream_in_stream_overloaded_error():
    body = event_line(
        "error",
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    respx.post(MESSAGES_URL).mock(return_value=sse_response(body))
    provider = AnthropicProvider(api_key="k")
    with pytest.raises(ProviderError) as err:
        await collect(provider.stream(make_request()))
    assert err.value.status_code == 529
    assert err.value.retryable is True


@pytest.mark.parametrize(
    ("status", "retryable"), [(401, False), (429, True), (500, True), (529, True)]
)
@respx.mock
async def test_error_mapping(status, retryable):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
            status, json={"error": {"type": "some_error", "message": "bad"}}
        )
    )
    provider = AnthropicProvider(api_key="sk-ant-secret")
    with pytest.raises(ProviderError) as err:
        await provider.generate(make_request())
    assert err.value.status_code == status
    assert err.value.retryable is retryable
    assert "sk-ant-secret" not in str(err.value)
