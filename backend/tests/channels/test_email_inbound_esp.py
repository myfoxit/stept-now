"""Per-ESP inbound endpoints: realistic fixture payloads → message rows,
signature verification (valid + invalid), and the SNS handshake."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx
import respx
from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.message import Message
from tests.channels.conftest import make_inbox, make_workspace

ADDRESS = "support@acme.com"


async def _email_inbox(secrets: dict | None = None) -> tuple[str, str, str]:
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id,
        channel_type="email",
        config={"address": ADDRESS},
        secrets=secrets,
        name="Email",
    )
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        token = inbox.config["webhook_token"]
    return workspace_id, inbox_id, token


async def _single_message(conversation_id: str) -> Message:
    async with get_session_factory()() as session:
        return (
            await session.execute(select(Message).where(Message.conversation_id == conversation_id))
        ).scalar_one()


# --- postmark ----------------------------------------------------------------

POSTMARK_PAYLOAD = {
    "FromFull": {"Email": "jane@example.com", "Name": "Jane Doe"},
    "ToFull": [{"Email": ADDRESS, "Name": "Acme Support"}],
    "Subject": "Password reset loop",
    "TextBody": "I keep getting sent back to login.\n\nOn Tue Sam wrote:\n> old stuff",
    "HtmlBody": "<p>I keep getting sent back to login.</p>",
    "MessageID": "0a129292-1111-4444-9999-abcdefabcdef",
    "Headers": [
        {"Name": "Message-ID", "Value": "<pm-inbound-1@example.com>"},
        {"Name": "X-Spam-Status", "Value": "No"},
    ],
}


async def test_postmark_inbound_creates_conversation(client: httpx.AsyncClient):
    workspace_id, inbox_id, token = await _email_inbox()
    response = await client.post(
        f"/api/channels/email/inbound/postmark/{inbox_id}/{token}", json=POSTMARK_PAYLOAD
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "created"
    conversation_id = response.json()["conversation_id"]

    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation.inbox_id == inbox_id
        assert conversation.subject == "Password reset loop"
        contact = await session.get(Contact, conversation.contact_id)
        assert contact.email == "jane@example.com"
        assert contact.workspace_id == workspace_id
    message = await _single_message(conversation_id)
    assert message.content == "I keep getting sent back to login."
    assert message.source_id == "<pm-inbound-1@example.com>"  # header beats bare MessageID


async def test_postmark_in_reply_to_threads(client: httpx.AsyncClient):
    _ws, inbox_id, token = await _email_inbox()
    first = await client.post(
        f"/api/channels/email/inbound/postmark/{inbox_id}/{token}", json=POSTMARK_PAYLOAD
    )
    conversation_id = first.json()["conversation_id"]
    reply = {
        **POSTMARK_PAYLOAD,
        "TextBody": "still stuck",
        "MessageID": "later-uuid",
        "Headers": [
            {"Name": "Message-ID", "Value": "<pm-inbound-2@example.com>"},
            {"Name": "In-Reply-To", "Value": "<pm-inbound-1@example.com>"},
        ],
    }
    second = await client.post(
        f"/api/channels/email/inbound/postmark/{inbox_id}/{token}", json=reply
    )
    assert second.json() == {"status": "appended", "conversation_id": conversation_id}


async def test_postmark_bad_token_404(client: httpx.AsyncClient):
    _ws, inbox_id, _token = await _email_inbox()
    response = await client.post(
        f"/api/channels/email/inbound/postmark/{inbox_id}/wrong", json=POSTMARK_PAYLOAD
    )
    assert response.status_code == 404


# --- sendgrid ----------------------------------------------------------------


async def test_sendgrid_inbound_parse_multipart(client: httpx.AsyncClient):
    _ws, inbox_id, token = await _email_inbox()
    response = await client.post(
        f"/api/channels/email/inbound/sendgrid/{inbox_id}/{token}",
        data={
            "from": "Jane Doe <jane@example.com>",
            "to": ADDRESS,
            "subject": "Invoice question",
            "text": "Where is invoice #42?",
            "html": "<p>Where is invoice #42?</p>",
            "headers": (
                "Received: by mx.sendgrid.net\r\n"
                "Message-ID: <sg-inbound-1@example.com>\r\n"
                "In-Reply-To: <does-not-exist@example.com>\r\n"
            ),
        },
        files={"attachment1": ("note.txt", b"binary ignored", "text/plain")},
    )
    assert response.status_code == 200, response.text
    conversation_id = response.json()["conversation_id"]
    message = await _single_message(conversation_id)
    assert message.content == "Where is invoice #42?"
    assert message.source_id == "<sg-inbound-1@example.com>"
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation.subject == "Invoice question"


# --- mailgun -----------------------------------------------------------------


def _mailgun_form(signing_key: str | None = None) -> dict[str, str]:
    fields = {
        "sender": "jane@example.com",
        "from": "Jane Doe <jane@example.com>",
        "recipient": ADDRESS,
        "subject": "API limits",
        "body-plain": "What are the API limits?\n> quoted",
        "stripped-text": "What are the API limits?",
        "Message-Id": "<mg-inbound-1@example.com>",
    }
    if signing_key:
        timestamp = str(int(time.time()))
        token = "mg-token-abc"
        fields.update(
            {
                "timestamp": timestamp,
                "token": token,
                "signature": hmac.new(
                    signing_key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256
                ).hexdigest(),
            }
        )
    return fields


async def test_mailgun_inbound_with_valid_signature(client: httpx.AsyncClient):
    _ws, inbox_id, token = await _email_inbox({"mailgun_signing_key": "mg-signing"})
    response = await client.post(
        f"/api/channels/email/inbound/mailgun/{inbox_id}/{token}",
        data=_mailgun_form("mg-signing"),
    )
    assert response.status_code == 200, response.text
    message = await _single_message(response.json()["conversation_id"])
    assert message.content == "What are the API limits?"
    assert message.source_id == "<mg-inbound-1@example.com>"


async def test_mailgun_inbound_invalid_signature_401(client: httpx.AsyncClient):
    _ws, inbox_id, token = await _email_inbox({"mailgun_signing_key": "mg-signing"})
    response = await client.post(
        f"/api/channels/email/inbound/mailgun/{inbox_id}/{token}",
        data=_mailgun_form("wrong-key"),
    )
    assert response.status_code == 401


async def test_mailgun_inbound_without_signing_key_accepts(client: httpx.AsyncClient):
    _ws, inbox_id, token = await _email_inbox()
    response = await client.post(
        f"/api/channels/email/inbound/mailgun/{inbox_id}/{token}", data=_mailgun_form()
    )
    assert response.status_code == 200
    assert response.json()["status"] == "created"


# --- resend ------------------------------------------------------------------

RESEND_PAYLOAD = {
    "type": "email.received",
    "created_at": "2026-08-07T12:00:00.000Z",
    "data": {
        "email_id": "b1946ac9-2222-4444-8888-abcdefabcdef",
        "from": "Jane Doe <jane@example.com>",
        "to": [ADDRESS],
        "subject": "Feature request",
        "text": "Please add dark mode.",
        "html": "<p>Please add dark mode.</p>",
        "message_id": "<resend-inbound-1@example.com>",
        "headers": [{"name": "In-Reply-To", "value": ""}],
    },
}


def _svix_headers(secret: str, body: bytes) -> dict[str, str]:
    msg_id = "msg_2abcdef"
    timestamp = str(int(time.time()))
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signature = base64.b64encode(
        hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return {
        "svix-id": msg_id,
        "svix-timestamp": timestamp,
        "svix-signature": f"v1,{signature}",
        "content-type": "application/json",
    }


async def test_resend_inbound_with_valid_svix_signature(client: httpx.AsyncClient):
    secret = "whsec_" + base64.b64encode(b"resend-endpoint-secret").decode()
    _ws, inbox_id, token = await _email_inbox({"resend_webhook_secret": secret})
    body = json.dumps(RESEND_PAYLOAD).encode()
    response = await client.post(
        f"/api/channels/email/inbound/resend/{inbox_id}/{token}",
        content=body,
        headers=_svix_headers(secret, body),
    )
    assert response.status_code == 200, response.text
    message = await _single_message(response.json()["conversation_id"])
    assert message.content == "Please add dark mode."
    assert message.source_id == "<resend-inbound-1@example.com>"


async def test_resend_inbound_invalid_signature_401(client: httpx.AsyncClient):
    secret = "whsec_" + base64.b64encode(b"resend-endpoint-secret").decode()
    other = "whsec_" + base64.b64encode(b"a-different-secret!!").decode()
    _ws, inbox_id, token = await _email_inbox({"resend_webhook_secret": secret})
    body = json.dumps(RESEND_PAYLOAD).encode()
    response = await client.post(
        f"/api/channels/email/inbound/resend/{inbox_id}/{token}",
        content=body,
        headers=_svix_headers(other, body),
    )
    assert response.status_code == 401


async def test_resend_non_inbound_event_ignored(client: httpx.AsyncClient):
    _ws, inbox_id, token = await _email_inbox()
    response = await client.post(
        f"/api/channels/email/inbound/resend/{inbox_id}/{token}",
        json={"type": "email.delivered", "data": {}},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


# --- ses (SNS) ---------------------------------------------------------------


def _mime(text: str, message_id: str, in_reply_to: str | None = None) -> bytes:
    headers = [
        "From: Jane Doe <jane@example.com>",
        f"To: {ADDRESS}",
        "Subject: SES delivered",
        f"Message-ID: {message_id}",
        'Content-Type: text/plain; charset="utf-8"',
    ]
    if in_reply_to:
        headers.append(f"In-Reply-To: {in_reply_to}")
    return ("\r\n".join(headers) + "\r\n\r\n" + text).encode()


def _sns_notification(raw_mime: bytes) -> dict:
    return {
        "Type": "Notification",
        "MessageId": "sns-1",
        "TopicArn": "arn:aws:sns:eu-west-1:123:inbound",
        "Message": json.dumps(
            {
                "notificationType": "Received",
                "mail": {"messageId": "ses-mail-1"},
                "receipt": {"action": {"type": "SNS"}},
                "content": base64.b64encode(raw_mime).decode(),
            }
        ),
    }


@respx.mock
async def test_sns_subscription_confirmation_handshake(client: httpx.AsyncClient):
    _ws, inbox_id, token = await _email_inbox()
    subscribe_url = "https://sns.eu-west-1.amazonaws.com/?Action=ConfirmSubscription&Token=abc"
    route = respx.get(subscribe_url).mock(return_value=httpx.Response(200, text="<xml/>"))
    response = await client.post(
        f"/api/channels/email/inbound/ses/{inbox_id}/{token}",
        content=json.dumps({"Type": "SubscriptionConfirmation", "SubscribeURL": subscribe_url}),
        headers={"x-amz-sns-message-type": "SubscriptionConfirmation"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "confirmed"}
    assert route.called


async def test_sns_subscription_confirmation_rejects_non_aws_url(client: httpx.AsyncClient):
    _ws, inbox_id, token = await _email_inbox()
    for bad in (
        "https://attacker.example.com/collect",
        "http://sns.eu-west-1.amazonaws.com/plain-http",
        "https://169.254.169.254/latest/meta-data",
    ):
        response = await client.post(
            f"/api/channels/email/inbound/ses/{inbox_id}/{token}",
            content=json.dumps({"Type": "SubscriptionConfirmation", "SubscribeURL": bad}),
            headers={"x-amz-sns-message-type": "SubscriptionConfirmation"},
        )
        assert response.status_code == 400, bad


async def test_ses_notification_parses_raw_mime(client: httpx.AsyncClient):
    _ws, inbox_id, token = await _email_inbox()
    raw = _mime("Hello from SES land.", "<ses-inbound-1@example.com>")
    response = await client.post(
        f"/api/channels/email/inbound/ses/{inbox_id}/{token}",
        content=json.dumps(_sns_notification(raw)),
        headers={"x-amz-sns-message-type": "Notification"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "created"
    message = await _single_message(response.json()["conversation_id"])
    assert message.content == "Hello from SES land."
    assert message.source_id == "<ses-inbound-1@example.com>"
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, response.json()["conversation_id"])
        assert conversation.subject == "SES delivered"


async def test_ses_notification_without_content_ignored(client: httpx.AsyncClient):
    _ws, inbox_id, token = await _email_inbox()
    payload = {
        "Type": "Notification",
        "Message": json.dumps(
            {"notificationType": "Received", "receipt": {"action": {"type": "S3"}}}
        ),
    }
    response = await client.post(
        f"/api/channels/email/inbound/ses/{inbox_id}/{token}",
        content=json.dumps(payload),
        headers={"x-amz-sns-message-type": "Notification"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
