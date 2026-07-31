"""Inbound Facebook Messenger + Instagram DM webhooks (Meta Graph platform).

Both channels share Meta's webhook shape, so this module hosts two routers:
``router`` (mounted at ``/api/channels/messenger``, payload ``object=="page"``)
and ``instagram_router`` (``/api/channels/instagram``, ``object=="instagram"``).
``GET /webhook/{inbox_id}`` answers the ``hub.*`` verify challenge; ``POST``
delivers ``entry[].messaging[]`` events.

Signature: ``X-Hub-Signature-256`` over the raw body is required and verified
when the inbox stores an ``app_secret`` secret; without one, requests are
accepted unsigned. Echoes of our own sends (``message.is_echo``) and
delivery/read receipts are skipped. Contact names use a PSID/IGSID suffix
placeholder — the optional Graph profile lookup is intentionally not performed
here. Importing this module also registers the outbound messenger and
instagram senders.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

import app.channels.messenger  # noqa: F401 — registers the "messenger"/"instagram" senders
from app.channels.messenger import verify_meta_signature
from app.core.deps import Db
from app.core.errors import NotFoundError, UnauthorizedError
from app.models.inbox import ChannelType, Inbox
from app.services import conversations as conversations_service
from app.services.inboxes import get_secrets

router = APIRouter()
instagram_router = APIRouter()


async def _get_inbox(session: AsyncSession, inbox_id: str, channel_type: ChannelType) -> Inbox:
    inbox = await session.get(Inbox, inbox_id)
    if inbox is None or inbox.channel_type != channel_type or not inbox.enabled:
        raise NotFoundError(f"{channel_type.value} inbox not found")
    return inbox


def _check_verify_token(inbox: Inbox, token: str) -> None:
    expected = str(inbox.config.get("webhook_verify_token") or "")
    if not expected or not hmac.compare_digest(expected, token):
        raise UnauthorizedError("Invalid verify token")


async def _verified_payload(request: Request, inbox: Inbox) -> dict[str, Any] | None:
    """Check the Meta signature over the raw body, then parse it (None = no-op)."""
    raw = await request.body()
    app_secret = get_secrets(inbox).get("app_secret")
    if not verify_meta_signature(raw, request.headers.get("X-Hub-Signature-256"), app_secret):
        raise UnauthorizedError("Invalid Meta signature")
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


async def _ingest_messaging_events(
    session: AsyncSession,
    inbox: Inbox,
    payload: dict[str, Any],
    *,
    expected_object: str,
    external_prefix: str,
    name_prefix: str,
) -> None:
    if payload.get("object") != expected_object:
        return
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for event in entry.get("messaging") or []:
            if not isinstance(event, dict):
                continue
            message = event.get("message")
            if not isinstance(message, dict) or message.get("is_echo"):
                continue  # delivery/read receipts and echoes of our own sends
            sender_id = str((event.get("sender") or {}).get("id") or "")
            if not sender_id:
                continue
            content = message.get("text")
            if not content:
                if not message.get("attachments"):
                    continue  # nothing ingestible
                content = "[Attachment]"
            await conversations_service.ingest_inbound(
                session,
                inbox,
                source_id=sender_id,
                content=content,
                contact_info={
                    "name": f"{name_prefix} {sender_id[-6:]}",
                    "external_id": f"{external_prefix}:{sender_id}",
                },
                message_source_id=message.get("mid"),
                meta={
                    f"{external_prefix}_sender_id": sender_id,
                    "timestamp": event.get("timestamp"),
                },
            )


# --- Facebook Messenger -----------------------------------------------------


@router.get("/webhook/{inbox_id}")
async def messenger_verify(
    inbox_id: str,
    session: Db,
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
) -> PlainTextResponse:
    inbox = await _get_inbox(session, inbox_id, ChannelType.MESSENGER)
    _check_verify_token(inbox, hub_verify_token)
    return PlainTextResponse(hub_challenge)


@router.post("/webhook/{inbox_id}")
async def messenger_webhook(inbox_id: str, request: Request, session: Db) -> dict[str, Any]:
    inbox = await _get_inbox(session, inbox_id, ChannelType.MESSENGER)
    payload = await _verified_payload(request, inbox)
    if payload is not None:
        await _ingest_messaging_events(
            session,
            inbox,
            payload,
            expected_object="page",
            external_prefix="messenger",
            name_prefix="Messenger user",
        )
    return {"ok": True}


# --- Instagram DM -----------------------------------------------------------


@instagram_router.get("/webhook/{inbox_id}")
async def instagram_verify(
    inbox_id: str,
    session: Db,
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
) -> PlainTextResponse:
    inbox = await _get_inbox(session, inbox_id, ChannelType.INSTAGRAM)
    _check_verify_token(inbox, hub_verify_token)
    return PlainTextResponse(hub_challenge)


@instagram_router.post("/webhook/{inbox_id}")
async def instagram_webhook(inbox_id: str, request: Request, session: Db) -> dict[str, Any]:
    inbox = await _get_inbox(session, inbox_id, ChannelType.INSTAGRAM)
    payload = await _verified_payload(request, inbox)
    if payload is not None:
        await _ingest_messaging_events(
            session,
            inbox,
            payload,
            expected_object="instagram",
            external_prefix="instagram",
            name_prefix="Instagram user",
        )
    return {"ok": True}
