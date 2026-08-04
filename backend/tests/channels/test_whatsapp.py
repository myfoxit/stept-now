"""WhatsApp Cloud API channel: verify challenge, Meta signature, inbound ingestion,
delivery statuses, outbound send + 24h session window."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select, update

from app.channels.whatsapp import GRAPH_BASE, send
from app.core.db import get_session_factory, utcnow
from app.core.events import Actor
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import ContactInbox, Inbox
from app.models.message import Message
from app.services import conversations as conversations_service
from tests.channels.conftest import make_inbox, make_outbound_message, make_workspace

VERIFY_TOKEN = "wa-verify-token"
APP_SECRET = "wa-app-secret"
API_KEY = "EAAG-graph-access-token"
PHONE_NUMBER_ID = "108765432101234"
MSISDN = "4915112345678"
WA_ID = "4915112345678"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _whatsapp_inbox(
    *, app_secret: str | None = APP_SECRET, allow_unsigned: bool = False
) -> tuple[str, str]:
    workspace_id = await make_workspace()
    secrets = {"api_key": API_KEY}
    if app_secret is not None:
        secrets["app_secret"] = app_secret
    config: dict = {"phone_number_id": PHONE_NUMBER_ID, "webhook_verify_token": VERIFY_TOKEN}
    if allow_unsigned:
        config["allow_unsigned"] = True
    inbox_id = await make_inbox(
        workspace_id,
        channel_type="whatsapp",
        config=config,
        secrets=secrets,
        name="WhatsApp",
    )
    return workspace_id, inbox_id


def _inbound(
    *,
    wamid: str = "wamid.IN-1",
    text: str = "hallo aus whatsapp",
    message: dict | None = None,
    name: str | None = "Ada Lovelace",
) -> dict:
    contact: dict = {"wa_id": WA_ID}
    if name is not None:
        contact["profile"] = {"name": name}
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550001111", "phone_number_id": PHONE_NUMBER_ID},
        "contacts": [contact],
        "messages": [
            message
            or {
                "from": MSISDN,
                "id": wamid,
                "timestamp": "1700000000",
                "type": "text",
                "text": {"body": text},
            }
        ],
    }
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "BA1", "changes": [{"field": "messages", "value": value}]}],
    }


def _status(wamid: str, status: str, errors: list | None = None) -> dict:
    entry: dict = {"id": wamid, "status": status, "timestamp": "1700000100"}
    if errors is not None:
        entry["errors"] = errors
    value = {"messaging_product": "whatsapp", "statuses": [entry]}
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "BA1", "changes": [{"field": "messages", "value": value}]}],
    }


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
        f"/api/channels/whatsapp/webhook/{inbox_id}", content=raw, headers=headers
    )


async def _conversation_id(inbox_id: str) -> str:
    async with get_session_factory()() as session:
        return (
            await session.execute(select(Conversation.id).where(Conversation.inbox_id == inbox_id))
        ).scalar_one()


async def _add_outbound(conversation_id: str, content: str) -> str:
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        message = await conversations_service.add_message(
            session,
            conversation,
            direction="out",
            author_type="user",
            author_id=None,
            author_name="Sam Support",
            content=content,
            actor=Actor.system(),
            deliver=False,
        )
        await session.commit()
        return message.id


# --- verify challenge -------------------------------------------------------


async def test_verify_challenge_ok(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    response = await client.get(
        f"/api/channels/whatsapp/webhook/{inbox_id}",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "challenge-42",
        },
    )
    assert response.status_code == 200
    assert response.text == "challenge-42"


async def test_verify_challenge_wrong_token_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    response = await client.get(
        f"/api/channels/whatsapp/webhook/{inbox_id}",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "c"},
    )
    assert response.status_code == 401


# --- signature --------------------------------------------------------------


async def test_webhook_invalid_signature_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    response = await _post(client, inbox_id, _inbound(), signature="sha256=deadbeef")
    assert response.status_code == 401


async def test_webhook_missing_signature_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    response = await _post(client, inbox_id, _inbound(), secret=None)
    assert response.status_code == 401


async def test_webhook_rejected_without_app_secret(client: httpx.AsyncClient):
    """Fail closed: no stored app secret means unsigned bodies are spoofable, so
    they are refused and nothing is ingested."""
    _workspace_id, inbox_id = await _whatsapp_inbox(app_secret=None)
    response = await _post(client, inbox_id, _inbound(), secret=None)
    assert response.status_code == 401, response.text
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(ContactInbox)
                .where(ContactInbox.inbox_id == inbox_id)
            )
        ).scalar_one()
    assert count == 0


async def test_webhook_accepted_unsigned_only_when_inbox_opts_in(client: httpx.AsyncClient):
    """Relays with no Meta app secret (360dialog & co) opt in explicitly."""
    _workspace_id, inbox_id = await _whatsapp_inbox(app_secret=None, allow_unsigned=True)
    response = await _post(client, inbox_id, _inbound(), secret=None)
    assert response.status_code == 200, response.text
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(ContactInbox)
                .where(ContactInbox.inbox_id == inbox_id)
            )
        ).scalar_one()
    assert count == 1


async def test_webhook_opt_in_does_not_weaken_a_configured_secret(client: httpx.AsyncClient):
    """allow_unsigned is an escape hatch for *missing* secrets, not a bypass."""
    _workspace_id, inbox_id = await _whatsapp_inbox(allow_unsigned=True)
    assert (await _post(client, inbox_id, _inbound(), secret=None)).status_code == 401
    assert (await _post(client, inbox_id, _inbound(), secret="wrong-secret")).status_code == 401
    assert (await _post(client, inbox_id, _inbound())).status_code == 200


async def test_webhook_disabled_inbox_404(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        assert inbox is not None
        inbox.enabled = False
        await session.commit()
    response = await _post(client, inbox_id, _inbound())
    assert response.status_code == 404


async def test_webhook_wrong_channel_type_404(client: httpx.AsyncClient):
    workspace_id = await make_workspace()
    telegram_inbox_id = await make_inbox(workspace_id, channel_type="telegram")
    response = await _post(client, telegram_inbox_id, _inbound())
    assert response.status_code == 404


# --- inbound messages -------------------------------------------------------


async def test_inbound_creates_conversation_and_contact(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    response = await _post(client, inbox_id, _inbound())
    assert response.status_code == 200, response.text

    async with get_session_factory()() as session:
        contact_inbox = (
            await session.execute(select(ContactInbox).where(ContactInbox.inbox_id == inbox_id))
        ).scalar_one()
        assert contact_inbox.source_id == MSISDN
        contact = await session.get(Contact, contact_inbox.contact_id)
        assert contact is not None
        assert contact.name == "Ada Lovelace"
        assert contact.external_id == f"whatsapp:{WA_ID}"
        message = (
            (await session.execute(select(Message).where(Message.content == "hallo aus whatsapp")))
            .scalars()
            .first()
        )
        assert message is not None
        assert message.source_id == "wamid.IN-1"
        assert message.meta.get("whatsapp_wa_id") == WA_ID


async def test_inbound_dedupes_on_redelivery(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    payload = _inbound(wamid="wamid.DUP", text="dup message")
    await _post(client, inbox_id, payload)
    await _post(client, inbox_id, payload)
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.content == "dup message")
            )
        ).scalar_one()
    assert count == 1


async def test_inbound_interactive_reply_content(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    message = {
        "from": MSISDN,
        "id": "wamid.INTERACTIVE",
        "timestamp": "1700000000",
        "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": "b1", "title": "Yes!"}},
    }
    await _post(client, inbox_id, _inbound(message=message))
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.content == "Yes!")
            )
        ).scalar_one()
    assert count == 1


async def test_inbound_unsupported_type_placeholder(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    message = {
        "from": MSISDN,
        "id": "wamid.UNSUP",
        "timestamp": "1700000000",
        "type": "unsupported",
    }
    await _post(client, inbox_id, _inbound(message=message))
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.content == "[Unsupported message type]")
            )
        ).scalar_one()
    assert count == 1


async def test_inbound_skips_reaction(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    message = {
        "from": MSISDN,
        "id": "wamid.REACT",
        "timestamp": "1700000000",
        "type": "reaction",
        "reaction": {"message_id": "wamid.IN-1", "emoji": "👍"},
    }
    response = await _post(client, inbox_id, _inbound(message=message))
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


# --- delivery statuses ------------------------------------------------------


async def _outbound_with_source_id(workspace_id: str, inbox_id: str, wamid: str) -> str:
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id=MSISDN, content="agent answer"
    )
    async with get_session_factory()() as session:
        message = await session.get(Message, message_id)
        assert message is not None
        message.source_id = wamid
        message.delivery_status = "pending"
        await session.commit()
    return message_id


async def test_status_marks_sent(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _whatsapp_inbox()
    message_id = await _outbound_with_source_id(workspace_id, inbox_id, "wamid.OUT-1")
    response = await _post(client, inbox_id, _status("wamid.OUT-1", "delivered"))
    assert response.status_code == 200, response.text
    async with get_session_factory()() as session:
        message = await session.get(Message, message_id)
        assert message is not None
        assert message.delivery_status == "sent"
        assert message.delivery_error is None


async def test_status_failed_records_error(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _whatsapp_inbox()
    message_id = await _outbound_with_source_id(workspace_id, inbox_id, "wamid.OUT-2")
    payload = _status(
        "wamid.OUT-2", "failed", errors=[{"code": 131047, "title": "Re-engagement message"}]
    )
    response = await _post(client, inbox_id, payload)
    assert response.status_code == 200, response.text
    async with get_session_factory()() as session:
        message = await session.get(Message, message_id)
        assert message is not None
        assert message.delivery_status == "failed"
        assert message.delivery_error == "Re-engagement message"


# --- outbound + 24h session window -----------------------------------------

SEND_URL = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages"


@respx.mock
async def test_outbound_sends_within_window(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    await _post(client, inbox_id, _inbound())  # fresh inbound opens the window
    conversation_id = await _conversation_id(inbox_id)
    message_id = await _add_outbound(conversation_id, "agent reply")

    route = respx.post(SEND_URL).mock(
        return_value=httpx.Response(
            200, json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.SENT-1"}]}
        )
    )
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        message = await session.get(Message, message_id)
        await send(session, inbox, message)
        assert message.source_id == "wamid.SENT-1"

    assert route.called
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "messaging_product": "whatsapp",
        "to": MSISDN,
        "type": "text",
        "text": {"body": "agent reply"},
    }
    assert route.calls.last.request.headers["authorization"] == f"Bearer {API_KEY}"


@respx.mock
async def test_outbound_raises_when_window_stale(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    await _post(client, inbox_id, _inbound())
    conversation_id = await _conversation_id(inbox_id)
    async with get_session_factory()() as session:  # age the inbound message beyond 24h
        await session.execute(
            update(Message)
            .where(Message.conversation_id == conversation_id)
            .values(created_at=utcnow() - timedelta(hours=25))
        )
        await session.commit()
    message_id = await _add_outbound(conversation_id, "too late")

    route = respx.post(SEND_URL).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(RuntimeError, match="24h WhatsApp session window"):
        async with get_session_factory()() as session:
            inbox = await session.get(Inbox, inbox_id)
            message = await session.get(Message, message_id)
            await send(session, inbox, message)
    assert not route.called


async def test_outbound_raises_without_any_inbound(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _whatsapp_inbox()
    _conversation_id_, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id=MSISDN, content="cold outreach"
    )
    with pytest.raises(RuntimeError, match="24h WhatsApp session window"):
        async with get_session_factory()() as session:
            inbox = await session.get(Inbox, inbox_id)
            message = await session.get(Message, message_id)
            await send(session, inbox, message)


@respx.mock
async def test_outbound_error_raises(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _whatsapp_inbox()
    await _post(client, inbox_id, _inbound())
    conversation_id = await _conversation_id(inbox_id)
    message_id = await _add_outbound(conversation_id, "x")

    respx.post(SEND_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Invalid OAuth access token", "code": 190}}
        )
    )
    with pytest.raises(RuntimeError, match="Invalid OAuth access token"):
        async with get_session_factory()() as session:
            inbox = await session.get(Inbox, inbox_id)
            message = await session.get(Message, message_id)
            await send(session, inbox, message)
