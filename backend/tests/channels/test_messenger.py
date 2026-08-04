"""Facebook Messenger channel: verify challenge, Meta signature, inbound ingestion
(echo/receipt skipping, attachment placeholder), outbound Graph send."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.channels.messenger import MESSENGER_SEND_URL, send
from app.core.db import get_session_factory
from app.models.contact import Contact
from app.models.inbox import ContactInbox, Inbox
from app.models.message import Message
from tests.channels.conftest import make_inbox, make_outbound_message, make_workspace

VERIFY_TOKEN = "fb-verify-token"
APP_SECRET = "fb-app-secret"
PAGE_TOKEN = "EAAB-page-access-token"
PAGE_ID = "1234509876"
PSID = "2412345678901"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _messenger_inbox(
    *, app_secret: str | None = APP_SECRET, allow_unsigned: bool = False
) -> tuple[str, str]:
    workspace_id = await make_workspace()
    secrets = {"page_access_token": PAGE_TOKEN}
    if app_secret is not None:
        secrets["app_secret"] = app_secret
    config: dict = {"page_id": PAGE_ID, "webhook_verify_token": VERIFY_TOKEN}
    if allow_unsigned:
        config["allow_unsigned"] = True
    inbox_id = await make_inbox(
        workspace_id,
        channel_type="messenger",
        config=config,
        secrets=secrets,
        name="Messenger",
    )
    return workspace_id, inbox_id


def _event(messaging: dict) -> dict:
    return {
        "object": "page",
        "entry": [{"id": PAGE_ID, "time": 1700000000, "messaging": [messaging]}],
    }


def _text_message(mid: str = "m_mid.1", text: str = "hi from messenger") -> dict:
    return _event(
        {
            "sender": {"id": PSID},
            "recipient": {"id": PAGE_ID},
            "timestamp": 1700000000,
            "message": {"mid": mid, "text": text},
        }
    )


async def _post(
    client: httpx.AsyncClient,
    inbox_id: str,
    payload: dict,
    *,
    secret: str | None = APP_SECRET,
    signature: str | None = None,
) -> httpx.Response:
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    elif secret is not None:
        headers["X-Hub-Signature-256"] = sign(secret, raw)
    return await client.post(
        f"/api/channels/messenger/webhook/{inbox_id}", content=raw, headers=headers
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


# --- verify challenge -------------------------------------------------------


async def test_verify_challenge_ok(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _messenger_inbox()
    response = await client.get(
        f"/api/channels/messenger/webhook/{inbox_id}",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "fb-challenge",
        },
    )
    assert response.status_code == 200
    assert response.text == "fb-challenge"


async def test_verify_challenge_wrong_token_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _messenger_inbox()
    response = await client.get(
        f"/api/channels/messenger/webhook/{inbox_id}",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "c"},
    )
    assert response.status_code == 401


# --- signature --------------------------------------------------------------


async def test_webhook_invalid_signature_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _messenger_inbox()
    response = await _post(client, inbox_id, _text_message(), signature="sha256=deadbeef")
    assert response.status_code == 401


async def test_webhook_rejected_without_app_secret(client: httpx.AsyncClient):
    """Fail closed — an unsigned body on a public endpoint is a spoofed message."""
    _workspace_id, inbox_id = await _messenger_inbox(app_secret=None)
    response = await _post(client, inbox_id, _text_message(), secret=None)
    assert response.status_code == 401, response.text
    assert await _contact_inbox_count(inbox_id) == 0


async def test_webhook_accepted_unsigned_only_when_inbox_opts_in(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _messenger_inbox(app_secret=None, allow_unsigned=True)
    response = await _post(client, inbox_id, _text_message(), secret=None)
    assert response.status_code == 200, response.text
    assert await _contact_inbox_count(inbox_id) == 1


# --- inbound ----------------------------------------------------------------


async def test_inbound_creates_conversation_and_contact(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _messenger_inbox()
    response = await _post(client, inbox_id, _text_message())
    assert response.status_code == 200, response.text

    async with get_session_factory()() as session:
        contact_inbox = (
            await session.execute(select(ContactInbox).where(ContactInbox.inbox_id == inbox_id))
        ).scalar_one()
        assert contact_inbox.source_id == PSID
        contact = await session.get(Contact, contact_inbox.contact_id)
        assert contact is not None
        assert contact.name == f"Messenger user {PSID[-6:]}"
        assert contact.external_id == f"messenger:{PSID}"
        message = (
            (await session.execute(select(Message).where(Message.content == "hi from messenger")))
            .scalars()
            .first()
        )
        assert message is not None
        assert message.source_id == "m_mid.1"


async def test_inbound_dedupes_on_redelivery(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _messenger_inbox()
    payload = _text_message(mid="m_dup", text="dup fb message")
    await _post(client, inbox_id, payload)
    await _post(client, inbox_id, payload)
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.content == "dup fb message")
            )
        ).scalar_one()
    assert count == 1


async def test_echo_and_receipt_events_skipped(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _messenger_inbox()
    echo = _event(
        {
            "sender": {"id": PAGE_ID},
            "recipient": {"id": PSID},
            "message": {"mid": "m_echo", "text": "our own reply", "is_echo": True},
        }
    )
    delivery = _event(
        {
            "sender": {"id": PSID},
            "recipient": {"id": PAGE_ID},
            "delivery": {"mids": ["m_mid.1"], "watermark": 1700000000},
        }
    )
    assert (await _post(client, inbox_id, echo)).status_code == 200
    assert (await _post(client, inbox_id, delivery)).status_code == 200
    assert await _contact_inbox_count(inbox_id) == 0


async def test_attachment_only_message_placeholder(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _messenger_inbox()
    payload = _event(
        {
            "sender": {"id": PSID},
            "recipient": {"id": PAGE_ID},
            "message": {
                "mid": "m_att",
                "attachments": [{"type": "image", "payload": {"url": "https://cdn/x.png"}}],
            },
        }
    )
    await _post(client, inbox_id, payload)
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.content == "[Attachment]")
            )
        ).scalar_one()
    assert count == 1


async def test_webhook_disabled_inbox_404(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _messenger_inbox()
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        assert inbox is not None
        inbox.enabled = False
        await session.commit()
    response = await _post(client, inbox_id, _text_message())
    assert response.status_code == 404


# --- outbound ---------------------------------------------------------------


@respx.mock
async def test_outbound_sends_message(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _messenger_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id=PSID, content="agent reply"
    )
    route = respx.post(MESSENGER_SEND_URL).mock(
        return_value=httpx.Response(200, json={"recipient_id": PSID, "message_id": "m_out.1"})
    )

    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        message = await session.get(Message, message_id)
        await send(session, inbox, message)
        assert message.source_id == "m_out.1"

    assert route.called
    request = route.calls.last.request
    assert request.url.params["access_token"] == PAGE_TOKEN
    assert json.loads(request.content) == {
        "recipient": {"id": PSID},
        "message": {"text": "agent reply"},
        "messaging_type": "RESPONSE",
    }


@respx.mock
async def test_outbound_error_raises(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _messenger_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id=PSID, content="x"
    )
    respx.post(MESSENGER_SEND_URL).mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "This person isn't available right now", "code": 551}},
        )
    )

    with pytest.raises(RuntimeError, match="isn't available"):
        async with get_session_factory()() as session:
            inbox = await session.get(Inbox, inbox_id)
            message = await session.get(Message, message_id)
            await send(session, inbox, message)
