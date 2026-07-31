"""Health endpoint + core utility behavior."""

import time

from app.ai.base import ChatMessage, ChatRequest, ToolSpec
from app.ai.local import LocalHashEmbedder, MockChatProvider
from app.core.db import uuid7
from app.core.pagination import decode_cursor, encode_cursor
from app.core.security import (
    compute_identity_hash,
    decrypt_secret,
    encrypt_secret,
    generate_api_key,
    hash_password,
    verify_identity_hash,
    verify_password,
)


async def test_healthz(client):
    response = await client.get("/api/v1/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_uuid7_is_time_ordered():
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()
    assert first < second
    assert len(first) == 36


def test_cursor_roundtrip():
    cursor = encode_cursor("2026-07-31T00:00:00", "abc")
    assert decode_cursor(cursor, 2) == ["2026-07-31T00:00:00", "abc"]


def test_password_hashing():
    hashed = hash_password("hunter2-hunter2")
    assert verify_password("hunter2-hunter2", hashed)
    assert not verify_password("wrong", hashed)


def test_secret_encryption_roundtrip():
    assert decrypt_secret(encrypt_secret("sk-super-secret")) == "sk-super-secret"


def test_api_key_shape():
    full, prefix, hashed = generate_api_key()
    assert full.startswith("sk_stept_")
    assert full.startswith(prefix[: len(prefix)])
    assert len(hashed) == 64


def test_identity_hash():
    digest = compute_identity_hash("secret", "user-42")
    assert verify_identity_hash("secret", "user-42", digest)
    assert not verify_identity_hash("secret", "user-43", digest)


async def test_hash_embedder_is_deterministic_and_normalized():
    embedder = LocalHashEmbedder(dim=64)
    [a1], [a2] = await embedder.embed(["refund policy"]), await embedder.embed(["refund policy"])
    assert a1 == a2
    norm = sum(v * v for v in a1) ** 0.5
    assert abs(norm - 1.0) < 1e-6
    # Related text should be closer than unrelated text.
    [b] = await embedder.embed(["our refund policy explained"])
    [c] = await embedder.embed(["quantum entanglement pizza"])
    cos = lambda x, y: sum(p * q for p, q in zip(x, y, strict=True))  # noqa: E731
    assert cos(a1, b) > cos(a1, c)


async def test_mock_provider_scripted_tool_call_then_answer():
    provider = MockChatProvider()
    tools = [ToolSpec(name="search_knowledge", description="", input_schema={"type": "object"})]
    request = ChatRequest(
        model="mock",
        messages=[ChatMessage.user('Hi [[tool:search_knowledge {"query": "refunds"}]]')],
        tools=tools,
    )
    result = await provider.generate(request)
    assert result.finish_reason == "tool_calls"
    call = result.tool_calls[0]
    assert call.name == "search_knowledge"
    assert call.input == {"query": "refunds"}

    # Feed the tool result back — the mock answers extractively with a citation.
    followup = ChatRequest(
        model="mock",
        messages=[
            ChatMessage.user('Hi [[tool:search_knowledge {"query": "refunds"}]]'),
            ChatMessage.assistant(None, [call]),
            ChatMessage.tool_result(call.id, '{"results": [{"content": "Refunds take 5 days."}]}'),
        ],
        tools=tools,
    )
    answer = await provider.generate(followup)
    assert answer.finish_reason == "stop"
    assert "Refunds take 5 days" in (answer.content or "")
    assert "[1]" in (answer.content or "")


async def test_mock_provider_streams_text():
    provider = MockChatProvider()
    request = ChatRequest(model="mock", messages=[ChatMessage.user("Hello there")])
    chunks = [event async for event in provider.stream(request)]
    assert chunks[-1].type == "finish"
    text = "".join(c.text for c in chunks if c.type == "text_delta")
    assert "Hello there" in text
