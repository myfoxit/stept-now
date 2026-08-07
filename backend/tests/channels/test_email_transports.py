"""Outbound transport dispatch: one test per provider against respx/fakes,
SigV4 shape, XOAUTH2 auth-string construction, token-seam failure mapping."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.channels import email_transports
from app.channels.email_transports import (
    POSTMARK_SEND_URL,
    RESEND_SEND_URL,
    SENDGRID_SEND_URL,
    EmailDeliveryError,
    IntegrationAuthError,
    _sigv4_headers,
    mailgun_endpoint,
    send_via_inbox,
    ses_endpoint,
    xoauth2_string,
)
from app.core.db import get_session_factory
from app.models.inbox import Inbox
from tests.channels.conftest import make_inbox, make_workspace

ADDRESS = "support@acme.com"

SEND_KWARGS = {
    "to": "jane@example.com",
    "subject": "Re: Login issue",
    "html": "<p>answer</p>",
    "text": "answer",
    "reply_to": "reply+c1@acme.com",
    "headers": {"Message-ID": "<mid-1@acme.com>", "References": "<orig@example.com>"},
}


async def _inbox(config: dict, secrets: dict | None = None) -> tuple[str, str]:
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id, channel_type="email", config=config, secrets=secrets, name="Email"
    )
    return workspace_id, inbox_id


async def _send(inbox_id: str, **overrides):
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        return await send_via_inbox(session, inbox, **{**SEND_KWARGS, **overrides})


# --- pure helpers -----------------------------------------------------------


def test_xoauth2_string_layout():
    decoded = base64.b64decode(xoauth2_string("agent@gmail.com", "ya29.token")).decode()
    assert decoded == "user=agent@gmail.com\x01auth=Bearer ya29.token\x01\x01"


def test_sigv4_headers_match_reference_computation():
    """Independent re-derivation of the AWS reference algorithm must agree."""
    now = datetime(2026, 8, 7, 12, 30, 45, tzinfo=UTC)
    body = b'{"x":1}'
    url = "https://email.eu-west-1.amazonaws.com/v2/email/outbound-emails"
    headers = _sigv4_headers(
        method="POST",
        url=url,
        region="eu-west-1",
        service="ses",
        access_key_id="AKIAEXAMPLE",
        secret_access_key="secretkey",
        body=body,
        now=now,
    )
    assert headers["X-Amz-Date"] == "20260807T123045Z"
    assert headers["Content-Type"] == "application/json"

    # Reference computation, written out step by step.
    canonical = "\n".join(
        [
            "POST",
            "/v2/email/outbound-emails",
            "",
            "content-type:application/json\n"
            "host:email.eu-west-1.amazonaws.com\n"
            "x-amz-date:20260807T123045Z\n",
            "content-type;host;x-amz-date",
            hashlib.sha256(body).hexdigest(),
        ]
    )
    scope = "20260807/eu-west-1/ses/aws4_request"
    to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            "20260807T123045Z",
            scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        ]
    )
    key = b"AWS4secretkey"
    for part in ("20260807", "eu-west-1", "ses", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    assert headers["Authorization"] == (
        f"AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/{scope}, "
        f"SignedHeaders=content-type;host;x-amz-date, Signature={signature}"
    )


# --- global -----------------------------------------------------------------


async def test_global_transport_uses_instance_sender(client, monkeypatch):
    _ws, inbox_id = await _inbox({"address": ADDRESS})  # transport defaults to global
    calls = []

    async def fake_send_email(
        to, subject, html, *, reply_to=None, from_override=None, headers=None
    ):
        calls.append({"to": to, "from_override": from_override, "headers": headers})
        return True

    monkeypatch.setattr(email_transports, "send_email", fake_send_email)
    result = await _send(inbox_id)
    assert result is None
    assert calls[0]["to"] == "jane@example.com"
    assert calls[0]["from_override"] == ADDRESS
    assert calls[0]["headers"]["Message-ID"] == "<mid-1@acme.com>"


async def test_global_transport_failure_raises(client, monkeypatch):
    _ws, inbox_id = await _inbox({"address": ADDRESS})

    async def failing(*args, **kwargs):
        return False

    monkeypatch.setattr(email_transports, "send_email", failing)
    with pytest.raises(EmailDeliveryError):
        await _send(inbox_id)


# --- smtp -------------------------------------------------------------------


async def test_smtp_transport_sends_with_inbox_credentials(client, monkeypatch):
    _ws, inbox_id = await _inbox(
        {
            "address": ADDRESS,
            "transport": "smtp",
            "smtp": {"host": "mail.acme.com", "port": 2525, "username": "acme", "security": "tls"},
        },
        {"smtp_password": "hunter2"},
    )
    captured = {}

    async def fake_smtp_send(message, **kwargs):
        captured["message"] = message
        captured.update(kwargs)

    monkeypatch.setattr(email_transports, "_smtp_send", fake_smtp_send)
    assert await _send(inbox_id) is None
    assert captured["hostname"] == "mail.acme.com"
    assert captured["port"] == 2525
    assert captured["username"] == "acme"
    assert captured["password"] == "hunter2"
    assert captured["use_tls"] is True
    assert captured["start_tls"] is None
    message = captured["message"]
    assert message["Message-ID"] == "<mid-1@acme.com>"
    assert message["References"] == "<orig@example.com>"
    assert message["Reply-To"] == "reply+c1@acme.com"


async def test_smtp_transport_wraps_smtp_errors(client, monkeypatch):
    import aiosmtplib

    _ws, inbox_id = await _inbox(
        {"address": ADDRESS, "transport": "smtp", "smtp": {"host": "mail.acme.com"}},
    )

    async def failing(message, **kwargs):
        raise aiosmtplib.SMTPException("boom")

    monkeypatch.setattr(email_transports, "_smtp_send", failing)
    with pytest.raises(EmailDeliveryError, match="smtp send failed"):
        await _send(inbox_id)


# --- gmail / microsoft (SMTP XOAUTH2) ----------------------------------------


class FakeSMTP:
    def __init__(self, auth_code: int = 235):
        self.auth_code = auth_code
        self.commands: list = []
        self.sent: list = []

    async def connect(self):
        self.commands.append("connect")

    async def ehlo(self):
        self.commands.append("ehlo")

    async def execute_command(self, *args):
        self.commands.append(args)
        return SimpleNamespace(code=self.auth_code, message="")

    async def send_message(self, message, recipients=None):
        self.sent.append((message, recipients))

    async def quit(self):
        self.commands.append("quit")

    def close(self):
        self.commands.append("close")


def _patch_tokens(monkeypatch, *, account_label="agent@gmail.com", token="tok-123", fail=False):
    connection = SimpleNamespace(id="conn-1", account_label=account_label)

    async def fake_get_connection(session, workspace_id, connection_id):
        assert connection_id == "conn-1"
        return connection

    async def fake_get_token(session, conn):
        if fail:
            raise IntegrationAuthError("refresh failed")
        return token

    monkeypatch.setattr(
        "app.integrations.tokens.get_connection", fake_get_connection, raising=False
    )
    monkeypatch.setattr(
        "app.integrations.tokens.get_valid_access_token", fake_get_token, raising=False
    )


async def test_gmail_transport_authenticates_with_xoauth2(client, monkeypatch):
    _ws, inbox_id = await _inbox(
        {"address": ADDRESS, "transport": "gmail", "connection_id": "conn-1"}
    )
    _patch_tokens(monkeypatch)
    fake = FakeSMTP()
    monkeypatch.setattr(email_transports, "_smtp_client", lambda host, port: fake)

    assert await _send(inbox_id) is None
    auth = next(c for c in fake.commands if isinstance(c, tuple) and c[0] == b"AUTH")
    assert auth[1] == b"XOAUTH2"
    assert auth[2] == xoauth2_string("agent@gmail.com", "tok-123").encode()
    message, recipients = fake.sent[0]
    assert recipients == ["jane@example.com"]
    assert message["Message-ID"] == "<mid-1@acme.com>"


async def test_xoauth2_auth_rejection_raises_reauth_hint(client, monkeypatch):
    _ws, inbox_id = await _inbox(
        {"address": ADDRESS, "transport": "microsoft", "connection_id": "conn-1"}
    )
    _patch_tokens(monkeypatch, account_label="agent@contoso.com")
    fake = FakeSMTP(auth_code=334)  # server pushes an error blob, then rejects
    monkeypatch.setattr(email_transports, "_smtp_client", lambda host, port: fake)

    with pytest.raises(EmailDeliveryError, match="Settings → Integrations"):
        await _send(inbox_id)
    assert (b"",) in [c for c in fake.commands if isinstance(c, tuple)]  # completed the exchange


async def test_expired_connection_maps_to_delivery_error(client, monkeypatch):
    _ws, inbox_id = await _inbox(
        {"address": ADDRESS, "transport": "gmail", "connection_id": "conn-1"}
    )
    _patch_tokens(monkeypatch, fail=True)
    with pytest.raises(EmailDeliveryError, match="reauthorize Gmail"):
        await _send(inbox_id)


# --- ses ---------------------------------------------------------------------


@respx.mock
async def test_ses_transport_signs_and_posts_raw_mime(client):
    _ws, inbox_id = await _inbox(
        {"address": ADDRESS, "transport": "ses", "ses": {"region": "eu-west-1"}},
        {"ses_access_key_id": "AKIAEXAMPLE", "ses_secret_access_key": "sk"},
    )
    route = respx.post(ses_endpoint("eu-west-1")).mock(
        return_value=httpx.Response(200, json={"MessageId": "ses-msg-1"})
    )
    assert await _send(inbox_id) == "ses-msg-1"

    request = route.calls.last.request
    auth = request.headers["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/")
    assert "/eu-west-1/ses/aws4_request" in auth
    assert "SignedHeaders=content-type;host;x-amz-date" in auth
    assert "Signature=" in auth
    assert request.headers["X-Amz-Date"].endswith("Z")

    body = json.loads(request.content)
    assert body["FromEmailAddress"] == ADDRESS
    assert body["Destination"] == {"ToAddresses": ["jane@example.com"]}
    raw = base64.b64decode(body["Content"]["Raw"]["Data"]).decode()
    assert "Message-ID: <mid-1@acme.com>" in raw
    assert "References: <orig@example.com>" in raw


@respx.mock
async def test_ses_error_response_raises(client):
    _ws, inbox_id = await _inbox(
        {"address": ADDRESS, "transport": "ses", "ses": {"region": "us-east-1"}},
        {"ses_access_key_id": "AKIA", "ses_secret_access_key": "sk"},
    )
    respx.post(ses_endpoint("us-east-1")).mock(
        return_value=httpx.Response(400, json={"message": "Email address is not verified."})
    )
    with pytest.raises(EmailDeliveryError, match="ses send failed"):
        await _send(inbox_id)


# --- resend ------------------------------------------------------------------


@respx.mock
async def test_resend_transport(client):
    _ws, inbox_id = await _inbox(
        {"address": ADDRESS, "transport": "resend"}, {"resend_api_key": "re_123"}
    )
    route = respx.post(RESEND_SEND_URL).mock(
        return_value=httpx.Response(200, json={"id": "resend-1"})
    )
    assert await _send(inbox_id) == "resend-1"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer re_123"
    body = json.loads(request.content)
    assert body["from"] == ADDRESS
    assert body["to"] == ["jane@example.com"]
    assert body["reply_to"] == "reply+c1@acme.com"
    assert body["headers"]["Message-ID"] == "<mid-1@acme.com>"


# --- postmark ----------------------------------------------------------------


@respx.mock
async def test_postmark_transport(client):
    _ws, inbox_id = await _inbox(
        {"address": ADDRESS, "transport": "postmark"}, {"postmark_server_token": "pm-token"}
    )
    route = respx.post(POSTMARK_SEND_URL).mock(
        return_value=httpx.Response(200, json={"MessageID": "pm-1"})
    )
    assert await _send(inbox_id) == "pm-1"
    request = route.calls.last.request
    assert request.headers["X-Postmark-Server-Token"] == "pm-token"
    body = json.loads(request.content)
    assert body["From"] == ADDRESS
    assert body["To"] == "jane@example.com"
    assert {"Name": "Message-ID", "Value": "<mid-1@acme.com>"} in body["Headers"]


# --- sendgrid ----------------------------------------------------------------


@respx.mock
async def test_sendgrid_transport(client):
    _ws, inbox_id = await _inbox(
        {"address": ADDRESS, "transport": "sendgrid"}, {"sendgrid_api_key": "SG.key"}
    )
    route = respx.post(SENDGRID_SEND_URL).mock(
        return_value=httpx.Response(202, headers={"X-Message-Id": "sg-1"})
    )
    assert await _send(inbox_id) == "sg-1"
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer SG.key"
    body = json.loads(request.content)
    assert body["personalizations"] == [{"to": [{"email": "jane@example.com"}]}]
    assert body["from"] == {"email": ADDRESS}
    assert {"type": "text/plain", "value": "answer"} in body["content"]
    assert body["headers"]["Message-ID"] == "<mid-1@acme.com>"


# --- mailgun -----------------------------------------------------------------


@respx.mock
async def test_mailgun_transport(client):
    _ws, inbox_id = await _inbox(
        {"address": ADDRESS, "transport": "mailgun", "mailgun": {"domain": "mg.acme.com"}},
        {"mailgun_api_key": "key-123"},
    )
    route = respx.post(mailgun_endpoint("mg.acme.com", "us")).mock(
        return_value=httpx.Response(200, json={"id": "<mg-1@mg.acme.com>"})
    )
    assert await _send(inbox_id) == "<mg-1@mg.acme.com>"
    request = route.calls.last.request
    expected_auth = "Basic " + base64.b64encode(b"api:key-123").decode()
    assert request.headers["Authorization"] == expected_auth
    content = request.content.decode()
    assert "h%3AReply-To=reply%2Bc1%40acme.com" in content
    assert "h%3AMessage-ID=%3Cmid-1%40acme.com%3E" in content


@respx.mock
async def test_mailgun_eu_base(client):
    _ws, inbox_id = await _inbox(
        {
            "address": ADDRESS,
            "transport": "mailgun",
            "mailgun": {"domain": "mg.acme.eu", "base": "eu"},
        },
        {"mailgun_api_key": "key-123"},
    )
    route = respx.post(mailgun_endpoint("mg.acme.eu", "eu")).mock(
        return_value=httpx.Response(200, json={"id": "<mg-2>"})
    )
    await _send(inbox_id)
    assert route.calls.last.request.url.host == "api.eu.mailgun.net"


# --- misconfiguration --------------------------------------------------------


async def test_missing_secret_raises_named_error(client):
    """Secrets removed after creation (rotation gone wrong) fail with a hint."""
    workspace_id = await make_workspace()
    inbox_id = await make_inbox(
        workspace_id,
        channel_type="email",
        config={"address": ADDRESS, "transport": "resend"},
        secrets={"resend_api_key": "re_1"},
    )
    async with get_session_factory()() as session:
        inbox = await session.get(Inbox, inbox_id)
        inbox.secrets_encrypted = None
        await session.commit()
    with pytest.raises(EmailDeliveryError, match="resend_api_key"):
        await _send(inbox_id)
