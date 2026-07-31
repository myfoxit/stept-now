"""Instagram DM channel: verify challenge, Meta signature, inbound ingestion
(object routing, echo skipping), outbound send via graph.instagram.com."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.channels.messenger import INSTAGRAM_SEND_URL, send_instagram
from app.core.db import get_session_factory
from app.models.contact import Contact
from app.models.inbox import ContactInbox, Inbox
from app.models.message import Message
from tests.channels.conftest import make_inbox, make_outbound_message, make_workspace

VERIFY_TOKEN = "ig-verify-token"
APP_SECRET = "ig-app-secret"
ACCESS_TOKEN = "IGAA-access-token"
INSTAGRAM_ID = "17841400000001"
IGSID = "6821954367801234"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _instagram_inbox() -> tuple[str, str]:
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id,
        channel_type="instagram",
        config={"instagram_id": INSTAGRAM_ID, "webhook_verify_token": VERIFY_TOKEN},
        secrets={"access_token": ACCESS_TOKEN, "app_secret": APP_SECRET},
        name="Instagram",
    )
    return workspace_id, inbox_id


def _event(messaging: dict, *, object_type: str = "instagram") -> dict:
    return {
        "object": object_type,
        "entry": [{"id": INSTAGRAM_ID, "time": 1700000000, "messaging": [messaging]}],
    }


def _text_message(mid: str = "ig_mid.1", text: str = "hi from instagram") -> dict:
    return _event(
        {
            "sender": {"id": IGSID},
            "recipient": {"id": INSTAGRAM_ID},
            "timestamp": 1700000000,
            "message": {"mid": mid, "text": text},
        }
    )


async def _post(
    client: httpx.AsyncClient,
    inbox_id: str,
    payload: dict,
    *,
    signature: str | None = None,
) -> httpx.Response:
    raw = json.dumps(payload).encode()
    return await client.post(
        f"/api/channels/instagram/webhook/{inbox_id}",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature if signature is not None else sign(APP_SECRET, raw),
        },
    )


async def _contact_inbox_count(inbox_id: str) -> int:
    async with get_session_factory()() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(ContactInbox)
                .where(ContactInbox.inbox_id == inbox_id)
            )
        ).scalar_one()


# --- verify challenge + signature -------------------------------------------


async def test_verify_challenge_ok(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _instagram_inbox()
    response = await client.get(
        f"/api/channels/instagram/webhook/{inbox_id}",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "ig-challenge",
        },
    )
    assert response.status_code == 200
    assert response.text == "ig-challenge"


async def test_verify_challenge_wrong_token_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _instagram_inbox()
    response = await client.get(
        f"/api/channels/instagram/webhook/{inbox_id}",
        params={"hub.mode": "subscribe", "hub.verify_token": "bad", "hub.challenge": "c"},
    )
    assert response.status_code == 401


async def test_webhook_invalid_signature_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _instagram_inbox()
    response = await _post(client, inbox_id, _text_message(), signature="sha256=deadbeef")
    assert response.status_code == 401


# --- inbound ----------------------------------------------------------------


async def test_inbound_creates_conversation_and_contact(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _instagram_inbox()
    response = await _post(client, inbox_id, _text_message())
    assert response.status_code == 200, response.text

    async with get_session_factory()() as session:
        contact_inbox = (
            await session.execute(select(ContactInbox).where(ContactInbox.inbox_id == inbox_id))
        ).scalar_one()
        assert contact_inbox.source_id == IGSID
        contact = await session.get(Contact, contact_inbox.contact_id)
        assert contact is not None
        assert contact.name == f"Instagram user {IGSID[-6:]}"
        assert contact.external_id == f"instagram:{IGSID}"
        message = (
            (await session.execute(select(Message).where(Message.content == "hi from instagram")))
            .scalars()
            .first()
        )
        assert message is not None
        assert message.source_id == "ig_mid.1"


async def test_inbound_dedupes_on_redelivery(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _instagram_inbox()
    payload = _text_message(mid="ig_dup", text="dup ig message")
    await _post(client, inbox_id, payload)
    await _post(client, inbox_id, payload)
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.content == "dup ig message")
            )
        ).scalar_one()
    assert count == 1


async def test_echo_event_skipped(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _instagram_inbox()
    echo = _event(
        {
            "sender": {"id": INSTAGRAM_ID},
            "recipient": {"id": IGSID},
            "message": {"mid": "ig_echo", "text": "our own dm", "is_echo": True},
        }
    )
    assert (await _post(client, inbox_id, echo)).status_code == 200
    assert await _contact_inbox_count(inbox_id) == 0


async def test_wrong_object_ignored(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _instagram_inbox()
    payload = _text_message()
    payload["object"] = "page"  # Messenger-shaped payload on the Instagram endpoint
    response = await _post(client, inbox_id, payload)
    assert response.status_code == 200
    assert await _contact_inbox_count(inbox_id) == 0


async def test_webhook_wrong_channel_type_404(client: httpx.AsyncClient):
    workspace_id = await make_workspace()
    messenger_inbox_id = await make_inbox(
        workspace_id,
        channel_type="messenger",
        config={"webhook_verify_token": VERIFY_TOKEN},
        secrets={"app_secret": APP_SECRET},
    )
    response = await _post(client, messenger_inbox_id, _text_message())
    assert response.status_code == 404


# --- outbound ---------------------------------------------------------------


@respx.mock
async def test_outbound_sends_message(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _instagram_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id=IGSID, content="dm reply"
    )
    route = respx.post(INSTAGRAM_SEND_URL).mock(
        return_value=httpx.Response(200, json={"recipient_id": IGSID, "message_id": "ig_out.1"})
    )

    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        message = await session.get(Message, message_id)
        await send_instagram(session, inbox, message)
        assert message.source_id == "ig_out.1"

    assert route.called
    request = route.calls.last.request
    assert request.url.params["access_token"] == ACCESS_TOKEN
    assert json.loads(request.content) == {
        "recipient": {"id": IGSID},
        "message": {"text": "dm reply"},
    }


@respx.mock
async def test_outbound_error_raises(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _instagram_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id=IGSID, content="x"
    )
    respx.post(INSTAGRAM_SEND_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Message failed to send", "code": 10}}
        )
    )

    with pytest.raises(RuntimeError, match="Message failed to send"):
        async with get_session_factory()() as session:
            inbox = await session.get(Inbox, inbox_id)
            message = await session.get(Message, message_id)
            await send_instagram(session, inbox, message)
