"""Slack channel: v0 signature verification, event ingestion, threaded outbound."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.channels.slack import POST_MESSAGE_URL, verify_signature
from app.core.db import get_session_factory
from app.models.inbox import ContactInbox
from app.models.message import Message
from tests.channels.conftest import make_inbox, make_outbound_message, make_workspace

SIGNING_SECRET = "slack-signing-secret"
BOT_TOKEN = "xoxb-test-token"
TEAM_ID = "T12345"


def sign(secret: str, timestamp: int | str, body: bytes) -> str:
    base = b"v0:" + str(timestamp).encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


async def _slack_inbox() -> tuple[str, str]:
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id,
        channel_type="slack",
        config={"team_id": TEAM_ID},
        secrets={"signing_secret": SIGNING_SECRET, "bot_token": BOT_TOKEN},
        name="Slack",
    )
    return workspace_id, inbox_id


async def _post_event(client: httpx.AsyncClient, body: dict, *, timestamp: int | None = None):
    raw = json.dumps(body).encode()
    ts = timestamp if timestamp is not None else int(time.time())
    return await client.post(
        "/api/channels/slack/events",
        content=raw,
        headers={
            "X-Slack-Request-Timestamp": str(ts),
            "X-Slack-Signature": sign(SIGNING_SECRET, ts, raw),
            "Content-Type": "application/json",
        },
    )


# --- signature helper -------------------------------------------------------


def test_verify_signature_valid():
    ts = int(time.time())
    body = b'{"ok":true}'
    assert verify_signature(SIGNING_SECRET, str(ts), body, sign(SIGNING_SECRET, ts, body)) is True


def test_verify_signature_wrong_secret():
    ts = int(time.time())
    body = b'{"ok":true}'
    assert verify_signature("other", str(ts), body, sign(SIGNING_SECRET, ts, body)) is False


def test_verify_signature_stale_timestamp():
    ts = int(time.time()) - 60 * 10
    body = b'{"ok":true}'
    assert verify_signature(SIGNING_SECRET, str(ts), body, sign(SIGNING_SECRET, ts, body)) is False


# --- inbound events ---------------------------------------------------------


async def test_url_verification_echoes_challenge(client: httpx.AsyncClient):
    await _slack_inbox()
    response = await client.post(
        "/api/channels/slack/events",
        json={"type": "url_verification", "challenge": "abc123"},
    )
    assert response.status_code == 200
    assert response.json() == {"challenge": "abc123"}


async def test_event_creates_conversation(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _slack_inbox()
    response = await _post_event(
        client,
        {
            "type": "event_callback",
            "team_id": TEAM_ID,
            "event": {
                "type": "message",
                "channel": "C001",
                "ts": "1700000000.000100",
                "user": "U777",
                "text": "hello from slack",
                "client_msg_id": "cmid-1",
            },
        },
    )
    assert response.status_code == 200, response.text

    async with get_session_factory()() as session:
        contact_inbox = (
            await session.execute(select(ContactInbox).where(ContactInbox.inbox_id == inbox_id))
        ).scalar_one()
        assert contact_inbox.source_id == "C001:1700000000.000100"
        message = (
            (await session.execute(select(Message).where(Message.content == "hello from slack")))
            .scalars()
            .first()
        )
        assert message is not None
        assert message.meta.get("slack_user") == "U777"


async def test_event_invalid_signature_401(client: httpx.AsyncClient):
    await _slack_inbox()
    raw = json.dumps({"type": "event_callback", "team_id": TEAM_ID, "event": {}}).encode()
    response = await client.post(
        "/api/channels/slack/events",
        content=raw,
        headers={
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=deadbeef",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


async def test_event_stale_timestamp_401(client: httpx.AsyncClient):
    await _slack_inbox()
    response = await _post_event(
        client,
        {"type": "event_callback", "team_id": TEAM_ID, "event": {"type": "message"}},
        timestamp=int(time.time()) - 60 * 10,
    )
    assert response.status_code == 401


async def test_bot_message_ignored(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _slack_inbox()
    await _post_event(
        client,
        {
            "type": "event_callback",
            "team_id": TEAM_ID,
            "event": {
                "type": "message",
                "channel": "C1",
                "ts": "1.1",
                "bot_id": "B1",
                "text": "echo",
            },
        },
    )
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(ContactInbox)
                .where(ContactInbox.inbox_id == inbox_id)
            )
        ).scalar_one()
    assert count == 0


async def test_event_dedupes_by_client_msg_id(client: httpx.AsyncClient):
    _workspace_id, _inbox_id = await _slack_inbox()
    event = {
        "type": "event_callback",
        "team_id": TEAM_ID,
        "event": {
            "type": "message",
            "channel": "C1",
            "ts": "1700000001.0",
            "user": "U1",
            "text": "dup",
            "client_msg_id": "same-id",
        },
    }
    await _post_event(client, event)
    await _post_event(client, event)
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.content == "dup")
            )
        ).scalar_one()
    assert count == 1


# --- outbound ---------------------------------------------------------------


@respx.mock
async def test_outbound_posts_to_thread(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _slack_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id="C500:thread-99", content="agent reply"
    )
    route = respx.post(POST_MESSAGE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    from app.channels.slack import send
    from app.models.inbox import Inbox

    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        message = await session.get(Message, message_id)
        await send(session, inbox, message)

    assert route.called
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"channel": "C500", "thread_ts": "thread-99", "text": "agent reply"}
    assert route.calls.last.request.headers["authorization"] == f"Bearer {BOT_TOKEN}"


@respx.mock
async def test_outbound_raises_when_not_ok(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _slack_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id="C1:t1", content="x"
    )
    respx.post(POST_MESSAGE_URL).mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )

    from app.channels.slack import send
    from app.models.inbox import Inbox

    with pytest.raises(RuntimeError):
        async with get_session_factory()() as session:
            inbox = await session.get(Inbox, inbox_id)
            message = await session.get(Message, message_id)
            await send(session, inbox, message)


# --- tenant isolation -------------------------------------------------------


async def test_unmatched_team_id_is_not_delivered_to_another_tenant(client: httpx.AsyncClient):
    """Slack addresses us by team, not by tenant, so the match must be exact.

    Two workspaces both run Slack. An event whose team_id claims neither must be
    dropped — not funnelled into whichever workspace configured Slack first.
    """
    from app.models.conversation import Conversation

    first_workspace_id, _first_inbox = await _slack_inbox()
    second_workspace_id = await make_workspace()
    await make_inbox(
        second_workspace_id,
        channel_type="slack",
        config={"team_id": "T_OTHER"},
        secrets={"signing_secret": SIGNING_SECRET, "bot_token": BOT_TOKEN},
        name="Slack Two",
    )

    response = await _post_event(
        client,
        {
            "type": "event_callback",
            "team_id": "T_NOBODY",
            "event": {
                "type": "message",
                "channel": "C9",
                "ts": "1.1",
                "user": "U9",
                "text": "should not land anywhere",
                "client_msg_id": "cross-tenant",
            },
        },
    )
    assert response.status_code == 404, response.text

    async with get_session_factory()() as session:
        for workspace_id in (first_workspace_id, second_workspace_id):
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(Conversation)
                    .where(Conversation.workspace_id == workspace_id)
                )
            ).scalar_one()
            assert count == 0, workspace_id


async def test_single_unclaimed_inbox_still_receives_events(client: httpx.AsyncClient):
    """Self-hosters who never filled in team_id keep working (one inbox only)."""
    from app.models.conversation import Conversation

    workspace_id = await make_workspace()
    await make_inbox(
        workspace_id,
        channel_type="slack",
        config={},
        secrets={"signing_secret": SIGNING_SECRET, "bot_token": BOT_TOKEN},
        name="Slack Unclaimed",
    )

    response = await _post_event(
        client,
        {
            "type": "event_callback",
            "team_id": "T_WHATEVER",
            "event": {
                "type": "message",
                "channel": "C1",
                "ts": "2.2",
                "user": "U1",
                "text": "hello",
                "client_msg_id": "unclaimed-1",
            },
        },
    )
    assert response.status_code == 200, response.text
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert count == 1
