"""Twilio SMS channel: signature-gated webhook + status callbacks, outbound REST send."""

from __future__ import annotations

import base64
from urllib.parse import parse_qsl

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.channels.sms import compute_twilio_signature
from app.core.config import get_settings
from app.core.db import get_session_factory
from app.models.contact import Contact
from app.models.inbox import ContactInbox, Inbox
from app.models.message import Message
from tests.channels.conftest import make_inbox, make_outbound_message, make_workspace

ACCOUNT_SID = "AC00000000000000000000000000000001"
AUTH_TOKEN = "twilio-auth-token"
PHONE_NUMBER = "+15550001111"
CONTACT_NUMBER = "+15557772222"


async def _sms_inbox() -> tuple[str, str]:
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id,
        channel_type="sms",
        config={"phone_number": PHONE_NUMBER},
        secrets={"account_sid": ACCOUNT_SID, "auth_token": AUTH_TOKEN},
        name="SMS",
    )
    return workspace_id, inbox_id


def _signed_headers(path: str, params: dict[str, str]) -> dict[str, str]:
    url = get_settings().public_base_url + path
    return {"X-Twilio-Signature": compute_twilio_signature(AUTH_TOKEN, url, params)}


def _inbound(
    sms_sid: str = "SMinbound1", body: str = "hi from sms", num_media: str = "0"
) -> dict[str, str]:
    return {
        "SmsSid": sms_sid,
        "AccountSid": ACCOUNT_SID,
        "From": CONTACT_NUMBER,
        "To": PHONE_NUMBER,
        "Body": body,
        "ProfileName": "Pat",
        "NumMedia": num_media,
    }


async def test_webhook_missing_signature_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _sms_inbox()
    response = await client.post(f"/api/channels/sms/webhook/{inbox_id}", data=_inbound())
    assert response.status_code == 401


async def test_webhook_invalid_signature_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _sms_inbox()
    response = await client.post(
        f"/api/channels/sms/webhook/{inbox_id}",
        data=_inbound(),
        headers={"X-Twilio-Signature": "bm90LXRoZS1yaWdodC1zaWc="},
    )
    assert response.status_code == 401


async def test_webhook_unknown_inbox_404(client: httpx.AsyncClient):
    response = await client.post("/api/channels/sms/webhook/does-not-exist", data=_inbound())
    assert response.status_code == 404


async def test_webhook_disabled_or_wrong_type_404(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _sms_inbox()
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        assert inbox is not None
        inbox.enabled = False
        await session.commit()
    path = f"/api/channels/sms/webhook/{inbox_id}"
    params = _inbound()
    response = await client.post(path, data=params, headers=_signed_headers(path, params))
    assert response.status_code == 404

    telegram_inbox_id = await make_inbox(
        workspace_id, channel_type="telegram", config={"webhook_secret": "s"}
    )
    response = await client.post(f"/api/channels/sms/webhook/{telegram_inbox_id}", data=_inbound())
    assert response.status_code == 404


async def test_inbound_creates_conversation(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _sms_inbox()
    path = f"/api/channels/sms/webhook/{inbox_id}"
    params = _inbound()
    response = await client.post(path, data=params, headers=_signed_headers(path, params))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Response></Response>" in response.text

    async with get_session_factory()() as session:
        contact_inbox = (
            await session.execute(select(ContactInbox).where(ContactInbox.inbox_id == inbox_id))
        ).scalar_one()
        assert contact_inbox.source_id == CONTACT_NUMBER
        contact = await session.get(Contact, contact_inbox.contact_id)
        assert contact is not None
        assert contact.name == "Pat"
        assert contact.attributes.get("phone") == CONTACT_NUMBER
        message = (
            (await session.execute(select(Message).where(Message.content == "hi from sms")))
            .scalars()
            .first()
        )
        assert message is not None
        assert message.source_id == "SMinbound1"


async def test_inbound_dedupes(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _sms_inbox()
    path = f"/api/channels/sms/webhook/{inbox_id}"
    params = _inbound(sms_sid="SMdup", body="dup message")
    headers = _signed_headers(path, params)
    await client.post(path, data=params, headers=headers)
    await client.post(path, data=params, headers=headers)
    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.content == "dup message")
            )
        ).scalar_one()
    assert count == 1


async def test_inbound_media_only_gets_placeholder(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _sms_inbox()
    path = f"/api/channels/sms/webhook/{inbox_id}"
    params = _inbound(sms_sid="SMmedia", body="", num_media="2")
    response = await client.post(path, data=params, headers=_signed_headers(path, params))
    assert response.status_code == 200
    async with get_session_factory()() as session:
        message = (
            (await session.execute(select(Message).where(Message.source_id == "SMmedia")))
            .scalars()
            .first()
        )
        assert message is not None
        assert message.content == "[Media message]"


async def _outbound_with_sid(workspace_id: str, inbox_id: str, sid: str) -> str:
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id=CONTACT_NUMBER, content="agent answer"
    )
    async with get_session_factory()() as session:
        message = await session.get(Message, message_id)
        assert message is not None
        message.source_id = sid
        message.delivery_status = "pending"
        await session.commit()
    return message_id


async def _reload_message(message_id: str) -> Message:
    async with get_session_factory()() as session:
        return (await session.execute(select(Message).where(Message.id == message_id))).scalar_one()


async def test_status_delivered_marks_sent(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _sms_inbox()
    message_id = await _outbound_with_sid(workspace_id, inbox_id, "SMout1")
    path = f"/api/channels/sms/status/{inbox_id}"
    params = {"MessageSid": "SMout1", "MessageStatus": "delivered"}
    response = await client.post(path, data=params, headers=_signed_headers(path, params))
    assert response.status_code == 200
    message = await _reload_message(message_id)
    assert message.delivery_status == "sent"
    assert message.delivery_error is None


async def test_status_failed_sets_error(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _sms_inbox()
    message_id = await _outbound_with_sid(workspace_id, inbox_id, "SMout2")
    path = f"/api/channels/sms/status/{inbox_id}"
    params = {"MessageSid": "SMout2", "MessageStatus": "failed", "ErrorCode": "30003"}
    response = await client.post(path, data=params, headers=_signed_headers(path, params))
    assert response.status_code == 200
    message = await _reload_message(message_id)
    assert message.delivery_status == "failed"
    assert message.delivery_error == "Twilio error 30003"


async def test_status_unknown_sid_noop(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _sms_inbox()
    message_id = await _outbound_with_sid(workspace_id, inbox_id, "SMout3")
    path = f"/api/channels/sms/status/{inbox_id}"
    params = {"MessageSid": "SMnope", "MessageStatus": "delivered"}
    response = await client.post(path, data=params, headers=_signed_headers(path, params))
    assert response.status_code == 200
    message = await _reload_message(message_id)
    assert message.delivery_status == "pending"  # untouched


async def test_status_invalid_signature_401(client: httpx.AsyncClient):
    _workspace_id, inbox_id = await _sms_inbox()
    response = await client.post(
        f"/api/channels/sms/status/{inbox_id}",
        data={"MessageSid": "SMout1", "MessageStatus": "delivered"},
        headers={"X-Twilio-Signature": "d3Jvbmc="},
    )
    assert response.status_code == 401


@respx.mock
async def test_outbound_sends_message(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _sms_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id=CONTACT_NUMBER, content="agent answer"
    )
    url = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json"
    route = respx.post(url).mock(
        return_value=httpx.Response(201, json={"sid": "SMnew99", "status": "queued"})
    )

    from app.channels.sms import send

    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        message = await session.get(Message, message_id)
        assert inbox is not None and message is not None
        await send(session, inbox, message)
        assert message.source_id == "SMnew99"

    assert route.called
    request = route.calls.last.request
    sent = dict(parse_qsl(request.content.decode()))
    assert sent == {
        "To": CONTACT_NUMBER,
        "From": PHONE_NUMBER,
        "Body": "agent answer",
        "StatusCallback": f"{get_settings().public_base_url}/api/channels/sms/status/{inbox_id}",
    }
    basic = base64.b64encode(f"{ACCOUNT_SID}:{AUTH_TOKEN}".encode()).decode()
    assert request.headers["Authorization"] == f"Basic {basic}"


@respx.mock
async def test_outbound_error_raises(client: httpx.AsyncClient):
    workspace_id, inbox_id = await _sms_inbox()
    _conversation_id, message_id = await make_outbound_message(
        workspace_id, inbox_id, contact_source_id=CONTACT_NUMBER, content="x"
    )
    url = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json"
    respx.post(url).mock(
        return_value=httpx.Response(
            400, json={"code": 21211, "message": "The 'To' number is not a valid phone number."}
        )
    )

    from app.channels.sms import send

    with pytest.raises(RuntimeError, match="not a valid phone number"):
        async with get_session_factory()() as session:
            inbox = await session.get(Inbox, inbox_id)
            message = await session.get(Message, message_id)
            assert inbox is not None and message is not None
            await send(session, inbox, message)
