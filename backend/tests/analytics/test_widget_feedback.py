"""Widget feedback endpoint: ownership, only outbound public replies are ratable."""

from __future__ import annotations

import httpx
from sqlalchemy import select

from app.core.db import get_session_factory, uuid7
from app.core.events import Actor
from app.models.conversation import Conversation
from app.models.search_analytics import MessageFeedback
from app.services import conversations as conversations_service
from tests.widget.conftest import auth_headers, boot, create_widget_setup


async def _visitor_conversation(
    client: httpx.AsyncClient, widget_key: str, visitor_id: str
) -> tuple[str, str]:
    token = (await boot(client, widget_key, visitor_id=visitor_id)).json()["token"]
    created = await client.post(
        "/api/widget/conversations",
        json={"message": "I need help with a refund"},
        headers=auth_headers(token),
    )
    assert created.status_code == 201, created.text
    return token, created.json()["id"]


async def _agent_reply(conversation_id: str, content: str, *, visibility: str = "public") -> str:
    """Inject an outbound message through the service layer; return its id."""
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        message = await conversations_service.add_message(
            session,
            conversation,
            direction="out",
            author_type="agent",
            author_id=None,
            author_name="Fin",
            content=content,
            visibility=visibility,
            actor=Actor(type="agent", id=None, label="Fin"),
            deliver=False,
        )
        message_id = message.id
        await session.commit()
    return message_id


def _url(conversation_id: str, message_id: str) -> str:
    return f"/api/widget/conversations/{conversation_id}/messages/{message_id}/feedback"


async def test_widget_feedback_happy_and_re_rate(client):
    widget = await create_widget_setup()
    token, conversation_id = await _visitor_conversation(client, widget.widget_key, "v1")
    message_id = await _agent_reply(conversation_id, "Refunds take 30 days.")

    response = await client.post(
        _url(conversation_id, message_id),
        json={"rating": "down", "comment": "did not help"},
        headers=auth_headers(token),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "rating": "down"}

    # re-rating updates the same row
    again = await client.post(
        _url(conversation_id, message_id), json={"rating": "up"}, headers=auth_headers(token)
    )
    assert again.status_code == 200
    assert again.json()["rating"] == "up"

    async with get_session_factory()() as session:
        rows = (
            (
                await session.execute(
                    select(MessageFeedback).where(MessageFeedback.message_id == message_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].rating == "up"
    assert rows[0].actor_type == "contact"
    assert rows[0].workspace_id == widget.workspace_id


async def test_widget_feedback_cross_contact_404(client):
    widget = await create_widget_setup()
    _token_a, conversation_id = await _visitor_conversation(client, widget.widget_key, "visitor-a")
    message_id = await _agent_reply(conversation_id, "Answer for A.")
    token_b = (await boot(client, widget.widget_key, visitor_id="visitor-b")).json()["token"]

    response = await client.post(
        _url(conversation_id, message_id), json={"rating": "up"}, headers=auth_headers(token_b)
    )
    assert response.status_code == 404


async def test_widget_feedback_rejects_inbound_message(client):
    widget = await create_widget_setup()
    token, conversation_id = await _visitor_conversation(client, widget.widget_key, "v1")
    messages = await client.get(
        f"/api/widget/conversations/{conversation_id}/messages", headers=auth_headers(token)
    )
    inbound = next(m for m in messages.json()["items"] if m["direction"] == "in")

    response = await client.post(
        _url(conversation_id, inbound["id"]), json={"rating": "up"}, headers=auth_headers(token)
    )
    assert response.status_code == 422  # contacts rate answers, not their own messages


async def test_widget_feedback_rejects_private_note(client):
    widget = await create_widget_setup()
    token, conversation_id = await _visitor_conversation(client, widget.widget_key, "v1")
    note_id = await _agent_reply(conversation_id, "internal note", visibility="note")

    response = await client.post(
        _url(conversation_id, note_id), json={"rating": "down"}, headers=auth_headers(token)
    )
    assert response.status_code == 422


async def test_widget_feedback_unknown_message_404(client):
    widget = await create_widget_setup()
    token, conversation_id = await _visitor_conversation(client, widget.widget_key, "v1")
    response = await client.post(
        _url(conversation_id, uuid7()), json={"rating": "up"}, headers=auth_headers(token)
    )
    assert response.status_code == 404


async def test_widget_feedback_requires_token_401(client):
    widget = await create_widget_setup()
    token, conversation_id = await _visitor_conversation(client, widget.widget_key, "v1")
    message_id = await _agent_reply(conversation_id, "Answer.")

    response = await client.post(_url(conversation_id, message_id), json={"rating": "up"})
    assert response.status_code == 401
