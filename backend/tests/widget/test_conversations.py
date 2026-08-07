"""Widget conversations: create/reply/read/unread, public-only visibility, ownership."""

from __future__ import annotations

import httpx

from tests.widget.conftest import (
    WidgetSetup,
    add_activity,
    agent_reply,
    auth_headers,
    boot,
    system_notice,
)


async def _boot_token(client: httpx.AsyncClient, widget: WidgetSetup, visitor_id: str) -> str:
    response = await boot(client, widget.widget_key, visitor_id=visitor_id)
    return response.json()["token"]


async def test_create_conversation_and_list(client: httpx.AsyncClient, widget: WidgetSetup):
    token = await _boot_token(client, widget, "v1")
    created = await client.post(
        "/api/widget/conversations",
        json={"message": "I need help with billing"},
        headers=auth_headers(token),
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    listed = await client.get("/api/widget/conversations", headers=auth_headers(token))
    assert listed.status_code == 200
    ids = [c["id"] for c in listed.json()]
    assert conversation_id in ids


async def test_reply_read_and_unread_cycle(client: httpx.AsyncClient, widget: WidgetSetup):
    token = await _boot_token(client, widget, "v1")
    created = await client.post(
        "/api/widget/conversations",
        json={"message": "first message"},
        headers=auth_headers(token),
    )
    conversation_id = created.json()["id"]

    # Agent replies (public) — visitor now has an unread message.
    await agent_reply(conversation_id, "Happy to help!")

    listed = await client.get("/api/widget/conversations", headers=auth_headers(token))
    summary = next(c for c in listed.json() if c["id"] == conversation_id)
    assert summary["unread"] is True
    assert summary["last_message_preview"] == "Happy to help!"

    read = await client.post(
        f"/api/widget/conversations/{conversation_id}/read", headers=auth_headers(token)
    )
    assert read.status_code == 200
    assert read.json()["unread"] is False

    listed = await client.get("/api/widget/conversations", headers=auth_headers(token))
    summary = next(c for c in listed.json() if c["id"] == conversation_id)
    assert summary["unread"] is False


async def test_send_us_a_message_always_opens_a_fresh_thread(
    client: httpx.AsyncClient, widget: WidgetSetup
):
    """POST /conversations is the 'Send us a message' button — it must open a new
    thread even when an earlier one is still open (Intercom behavior)."""
    token = await _boot_token(client, widget, "v1")
    first = await client.post(
        "/api/widget/conversations", json={"message": "first thread"}, headers=auth_headers(token)
    )
    second = await client.post(
        "/api/widget/conversations", json={"message": "second thread"}, headers=auth_headers(token)
    )
    assert first.json()["id"] != second.json()["id"]

    # The new thread contains only its own opening message.
    messages = await client.get(
        f"/api/widget/conversations/{second.json()['id']}/messages", headers=auth_headers(token)
    )
    contents = [m["content"] for m in messages.json()["items"]]
    assert contents == ["second thread"]

    # A plain follow-up (POST .../messages) still lands on the same thread.
    follow = await client.post(
        f"/api/widget/conversations/{second.json()['id']}/messages",
        json={"message": "still the second thread"},
        headers=auth_headers(token),
    )
    assert follow.status_code == 201


async def test_public_system_notice_is_visible_to_the_visitor(
    client: httpx.AsyncClient, widget: WidgetSetup
):
    """The engine tells a visitor about a handoff via a public system message —
    it must come back through the widget (unlike activity/notes)."""
    token = await _boot_token(client, widget, "v1")
    created = await client.post(
        "/api/widget/conversations", json={"message": "help"}, headers=auth_headers(token)
    )
    conversation_id = created.json()["id"]
    await system_notice(conversation_id, "You're being connected to a teammate.")

    messages = await client.get(
        f"/api/widget/conversations/{conversation_id}/messages", headers=auth_headers(token)
    )
    items = messages.json()["items"]
    notice = next(m for m in items if m["author_type"] == "system")
    assert notice["content"] == "You're being connected to a teammate."


async def test_public_only_pagination_never_returns_a_short_page(
    client: httpx.AsyncClient, widget: WidgetSetup
):
    """A run of notes/activity between public messages must not shrink a page or
    strand earlier public messages behind an empty one (the post-filter bug)."""
    token = await _boot_token(client, widget, "v1")
    created = await client.post(
        "/api/widget/conversations", json={"message": "m0"}, headers=auth_headers(token)
    )
    conversation_id = created.json()["id"]
    # Interleave 5 public replies with a wall of internal-only entries.
    for i in range(1, 6):
        await add_activity(conversation_id, f"internal activity {i}")
        await agent_reply(conversation_id, f"internal note {i}", visibility="note")
        await agent_reply(conversation_id, f"public {i}")

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # bounded walk
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = await client.get(
            f"/api/widget/conversations/{conversation_id}/messages",
            params=params,
            headers=auth_headers(token),
        )
        body = page.json()
        for m in body["items"]:
            assert "internal" not in m["content"]  # never a note/activity
        seen.extend(m["content"] for m in body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    # All 6 public messages recovered (pages arrive newest-first), nothing dropped.
    assert sorted(seen) == ["m0", "public 1", "public 2", "public 3", "public 4", "public 5"]


async def test_visitor_can_post_followup_message(client: httpx.AsyncClient, widget: WidgetSetup):
    token = await _boot_token(client, widget, "v1")
    created = await client.post(
        "/api/widget/conversations", json={"message": "hello"}, headers=auth_headers(token)
    )
    conversation_id = created.json()["id"]

    reply = await client.post(
        f"/api/widget/conversations/{conversation_id}/messages",
        json={"message": "one more thing"},
        headers=auth_headers(token),
    )
    assert reply.status_code == 201, reply.text
    assert reply.json()["direction"] == "in"
    assert reply.json()["content"] == "one more thing"


async def test_messages_exclude_notes_and_activity(client: httpx.AsyncClient, widget: WidgetSetup):
    token = await _boot_token(client, widget, "v1")
    created = await client.post(
        "/api/widget/conversations",
        json={"message": "public question"},
        headers=auth_headers(token),
    )
    conversation_id = created.json()["id"]

    await agent_reply(conversation_id, "public answer")
    await agent_reply(conversation_id, "secret internal note", visibility="note")
    await add_activity(conversation_id, "Sam resolved the conversation")

    messages = await client.get(
        f"/api/widget/conversations/{conversation_id}/messages", headers=auth_headers(token)
    )
    assert messages.status_code == 200
    contents = [m["content"] for m in messages.json()["items"]]
    assert "public question" in contents
    assert "public answer" in contents
    assert "secret internal note" not in contents
    assert "Sam resolved the conversation" not in contents
    for message in messages.json()["items"]:
        assert message["author_type"] != "system"


async def test_message_out_strips_meta_to_citations(client: httpx.AsyncClient, widget: WidgetSetup):
    token = await _boot_token(client, widget, "v1")
    created = await client.post(
        "/api/widget/conversations", json={"message": "q"}, headers=auth_headers(token)
    )
    conversation_id = created.json()["id"]

    # Inject an agent message carrying citations + internal metadata.
    from app.core.db import get_session_factory
    from app.core.events import Actor
    from app.models.conversation import Conversation
    from app.services import conversations as conversations_service

    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        await conversations_service.add_message(
            session,
            conversation,
            direction="out",
            author_type="agent",
            author_id=None,
            author_name="AI",
            content="answer with source",
            meta={"citations": [{"n": 1, "title": "Doc"}], "agent_run_id": "secret-run"},
            actor=Actor(type="agent", id=None, label="AI"),
            deliver=False,
        )
        await session.commit()

    messages = await client.get(
        f"/api/widget/conversations/{conversation_id}/messages", headers=auth_headers(token)
    )
    agent_message = next(
        m for m in messages.json()["items"] if m["content"] == "answer with source"
    )
    assert agent_message["meta"] == {"citations": [{"n": 1, "title": "Doc"}]}
    assert "agent_run_id" not in agent_message["meta"]


async def test_typing_returns_204(client: httpx.AsyncClient, widget: WidgetSetup):
    token = await _boot_token(client, widget, "v1")
    created = await client.post(
        "/api/widget/conversations", json={"message": "q"}, headers=auth_headers(token)
    )
    conversation_id = created.json()["id"]
    response = await client.post(
        f"/api/widget/conversations/{conversation_id}/typing",
        json={"is_typing": True},
        headers=auth_headers(token),
    )
    assert response.status_code == 204


async def test_missing_token_401(client: httpx.AsyncClient, widget: WidgetSetup):
    response = await client.get("/api/widget/conversations")
    assert response.status_code == 401


async def test_invalid_token_401(client: httpx.AsyncClient, widget: WidgetSetup):
    response = await client.get(
        "/api/widget/conversations", headers=auth_headers("not-a-real-token")
    )
    assert response.status_code == 401


async def test_cross_contact_ownership_404(client: httpx.AsyncClient, widget: WidgetSetup):
    token_a = await _boot_token(client, widget, "visitor-a")
    token_b = await _boot_token(client, widget, "visitor-b")
    created = await client.post(
        "/api/widget/conversations", json={"message": "mine"}, headers=auth_headers(token_a)
    )
    conversation_id = created.json()["id"]

    # Visitor B must not read or write into visitor A's conversation.
    read = await client.get(
        f"/api/widget/conversations/{conversation_id}/messages", headers=auth_headers(token_b)
    )
    assert read.status_code == 404
    write = await client.post(
        f"/api/widget/conversations/{conversation_id}/messages",
        json={"message": "sneaky"},
        headers=auth_headers(token_b),
    )
    assert write.status_code == 404
