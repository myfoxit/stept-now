"""search_conversations / get_conversation / add_conversation_note."""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.audit import AuditLog
from app.models.message import Message
from tests.mcp.conftest import call_tool, make_api_key, seed_conversation

WRITE_SCOPES = ["read", "write"]


async def test_search_and_get_conversation(client, workspace_ctx):
    conversation_id = await seed_conversation(workspace_ctx.id)
    key = (await make_api_key(client, workspace_ctx))["key"]

    results = await call_tool(client, "search_conversations", {"query": "Nina"}, key=key)
    assert [r["id"] for r in results] == [conversation_id]
    assert results[0]["subject"] == "Billing question"
    assert results[0]["status"] == "open"
    assert results[0]["contact"]["name"] == "Nina Doe"
    assert results[0]["contact"]["email"] == "nina@example.com"
    assert results[0]["snippet"] == "Hello, I need help with billing"
    assert results[0]["last_message_at"] is not None

    none_resolved = await call_tool(client, "search_conversations", {"status": "resolved"}, key=key)
    assert none_resolved == []

    conversation = await call_tool(
        client, "get_conversation", {"conversation_id": conversation_id}, key=key
    )
    assert conversation["id"] == conversation_id
    assert conversation["number"] == 1
    assert conversation["contact"]["name"] == "Nina Doe"
    assert [m["content"] for m in conversation["messages"]] == ["Hello, I need help with billing"]
    assert conversation["messages"][0]["is_note"] is False
    assert conversation["messages"][0]["author"] == {"type": "contact", "name": "Nina Doe"}

    missing = await call_tool(client, "get_conversation", {"conversation_id": "nope"}, key=key)
    assert missing == {"error": "Conversation not found"}


async def test_add_conversation_note_persists_flags_and_audits(client, workspace_ctx):
    conversation_id = await seed_conversation(workspace_ctx.id)
    key_body = await make_api_key(client, workspace_ctx, scopes=WRITE_SCOPES, name="Notes key")
    key = key_body["key"]

    note = await call_tool(
        client,
        "add_conversation_note",
        {"conversation_id": conversation_id, "body": "Customer is on the annual plan."},
        key=key,
    )
    assert note["conversation_id"] == conversation_id
    assert note["visibility"] == "note"

    async with get_session_factory()() as session:
        message = await session.get(Message, note["id"])
        assert message is not None
        assert message.visibility == "note"
        assert message.content == "Customer is on the annual plan."
        assert message.author_name == "API key Notes key"

        entry = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.workspace_id == workspace_ctx.id,
                    AuditLog.action == "conversation.note.create",
                    AuditLog.target_id == conversation_id,
                )
            )
        ).scalar_one()
        assert entry.actor_type == "api_key"
        assert entry.actor_id == key_body["id"]
        assert entry.meta["message_id"] == note["id"]

    # The note is included and flagged when reading the thread back.
    conversation = await call_tool(
        client, "get_conversation", {"conversation_id": conversation_id}, key=key
    )
    notes = [m for m in conversation["messages"] if m["is_note"]]
    assert len(notes) == 1
    assert notes[0]["visibility"] == "note"
    assert notes[0]["content"] == "Customer is on the annual plan."


async def test_add_note_requires_write_scope(client, workspace_ctx):
    conversation_id = await seed_conversation(workspace_ctx.id)
    key = (await make_api_key(client, workspace_ctx, scopes=["read"]))["key"]
    payload = await call_tool(
        client,
        "add_conversation_note",
        {"conversation_id": conversation_id, "body": "nope"},
        key=key,
    )
    assert payload["error"].startswith("This API key lacks the conversations:write permission")

    async with get_session_factory()() as session:
        notes = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id == conversation_id, Message.visibility == "note"
                )
            )
        ).scalars()
        assert list(notes) == []


async def test_add_note_rejects_empty_body(client, workspace_ctx):
    conversation_id = await seed_conversation(workspace_ctx.id)
    key = (await make_api_key(client, workspace_ctx, scopes=WRITE_SCOPES))["key"]
    payload = await call_tool(
        client,
        "add_conversation_note",
        {"conversation_id": conversation_id, "body": "   "},
        key=key,
    )
    assert payload == {"error": "Note body must not be empty"}
