"""LINE channel: raw-body signature gate, profile lookup + fallback, push sender."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.channels.line import compute_line_signature
from app.core.db import get_session_factory
from app.models.contact import Contact
from app.models.inbox import ContactInbox, Inbox
from app.models.message import Message
from tests.channels.conftest import make_inbox, make_outbound_message, make_workspace

CHANNEL_SECRET = "line-channel-secret"
CHANNEL_TOKEN = "line-channel-token"
USER_ID = "U4af4980629abcdef1234567890"
PROFILE_URL = f"https://api.line.me/v2/bot/profile/{USER_ID}"
PUSH_URL = "https://api.line.me/v2/bot/message/push"


async def _line_inbox() -> tuple[str, str]:
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id,
        channel_type="line",
        config={},
        secrets={"channel_secret": CHANNEL_SECRET, "channel_token": CHANNEL_TOKEN},
        name="LINE",
    )
    return workspace_id, inbox_id


def _payload(text: str = "hello from line", message_id: str = "468789577898262530") -> dict:
    return {
        "destination": "U-bot-destination",
        "events": [
            {
                "type": "message",
                "replyToken": "reply-token",
                "timestamp": 1462629479859,
                "source": {"type": "user", "userId": USER_ID},
                "message": {"id": message_id, "type": "text", "text": text},
            }
        ],
    }


async def _post_signed(
    client: httpx.AsyncClient, inbox_id: str, payload: dict, *, signature: str | None = None
) -> httpx.Response:
    raw = json.dumps(payload).encode()
    sig = signature if signature is not None else compute_line_signature(CHANNEL_SECRET, raw)
    return await client.post(
        f"/api/channels/line/webhook/{inbox_id}",
        content=raw,
        headers={"x-line-signature": sig, "content-type": "application/json"},
    )


def _mock_profile(status_code: int = 200) -> respx.Route:
    body = {"displayName": "Naomi", "userId": USER_ID} if status_code == 200 else {"message": "x"}
    return respx.get(PROFILE_URL).mock(return_value=httpx.Response(status_code, json=body))


async def test_webhook_missing_signature_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _line_inbox()
    response = await client.post(
        f"/api/channels/line/webhook/{inbox_id}",
        content=json.dumps(_payload()).encode(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 401


async def test_webhook_invalid_signature_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _line_inbox()
    response = await _post_signed(client, inbox_id, _payload(), signature="bm90LXZhbGlk")
    assert response.status_code == 401


async def test_webhook_unknown_inbox_404(client: httpx.AsyncClient):
    response = await _post_signed(client, "does-not-exist", _payload())
    assert response.status_code == 404


async def test_webhook_disabled_or_wrong_type_404(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _line_inbox()
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        assert inbox is not None
        inbox.enabled = False
        await session.commit()
    response = await _post_signed(client, inbox_id, _payload())
    assert response.status_code == 404

    telegram_inbox_id = await make_inbox(
        workspace_id, channel_type="telegram", config={"webhook_secret": "s"}
    )
    response = await _post_signed(client, telegram_inbox_id, _payload())
    assert response.status_code == 404


@respx.mock
async def test_inbound_creates_conversation(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _line_inbox()
    profile_route = _mock_profile(200)

    response = await _post_signed(client, inbox_id, _payload())
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert profile_route.called
    assert profile_route.calls.last.request.headers["Authorization"] == f"Bearer {CHANNEL_TOKEN}"

    async with get_session_factory()() as session:
        contact_inbox = (
            await session.execute(select(ContactInbox).where(ContactInbox.inbox_id == inbox_id))
        ).scalar_one()
        assert contact_inbox.source_id == USER_ID
        contact = await session.get(Contact, contact_inbox.contact_id)
        assert contact is not None
        assert contact.name == "Naomi"
        assert contact.external_id == f"line:{USER_ID}"
        message = (
            (await session.execute(select(Message).where(Message.content == "hello from line")))
            .scalars()
            .first()
        )
        assert message is not None
        assert message.source_id == "468789577898262530"


@respx.mock
async def test_inbound_dedupes(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _line_inbox()
    _mock_profile(200)
    payload = _payload(text="dup line message", message_id="999888777")
    await _post_signed(client, inbox_id, payload)
    await _post_signed(client, inbox_id, payload)
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.content == "dup line message")
            )
        ).scalar_one()
    assert count == 1


@respx.mock
async def test_inbound_profile_failure_falls_back_to_generated_name(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _line_inbox()
    _mock_profile(500)

    response = await _post_signed(client, inbox_id, _payload())
    assert response.status_code == 200

    async with get_session_factory()() as session:
        contact_inbox = (
            await session.execute(select(ContactInbox).where(ContactInbox.inbox_id == inbox_id))
        ).scalar_one()
        contact = await session.get(Contact, contact_inbox.contact_id)
        assert contact is not None
        assert contact.name == f"LINE user {USER_ID[-6:]}"


async def test_inbound_skips_non_text_events(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _line_inbox()
    payload = {
        "events": [
            {
                "type": "message",
                "source": {"type": "user", "userId": USER_ID},
                "message": {"id": "111", "type": "image"},
            },
            {"type": "follow", "source": {"type": "user", "userId": USER_ID}},
        ]
    }
    response = await _post_signed(client, inbox_id, payload)
    assert response.status_code == 200
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(ContactInbox)
                .where(ContactInbox.inbox_id == inbox_id)
            )
        ).scalar_one()
    assert count == 0


@respx.mock
async def test_outbound_sends_message(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _line_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id=USER_ID, content="agent answer"
    )
    route = respx.post(PUSH_URL).mock(return_value=httpx.Response(200, json={}))

    from app.channels.line import send

    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        message = await session.get(Message, message_id)
        assert inbox is not None and message is not None
        await send(session, inbox, message)

    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {CHANNEL_TOKEN}"
    assert json.loads(request.content) == {
        "to": USER_ID,
        "messages": [{"type": "text", "text": "agent answer"}],
    }


@respx.mock
async def test_outbound_error_raises(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _line_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id=USER_ID, content="x"
    )
    respx.post(PUSH_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "message": "The request body has 1 error(s)",
                "details": [{"message": "May not be empty", "property": "messages[0].text"}],
            },
        )
    )

    from app.channels.line import send

    with pytest.raises(RuntimeError, match=r"has 1 error\(s\): May not be empty"):
        async with get_session_factory()() as session:
            inbox = await session.get(Inbox, inbox_id)
            message = await session.get(Message, message_id)
            assert inbox is not None and message is not None
            await send(session, inbox, message)
