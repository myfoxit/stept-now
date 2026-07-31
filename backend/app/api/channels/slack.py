"""Inbound Slack events webhook.

Handles the ``url_verification`` handshake, verifies the v0 request signature
against the routed inbox's signing secret, and ingests human message events as
inbound messages (threaded by ``channel:thread_ts``). Importing this module also
registers the outbound Slack sender.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.channels.slack  # noqa: F401 — registers the outbound "slack" sender
from app.channels.slack import verify_signature
from app.core.deps import Db
from app.core.errors import NotFoundError, UnauthorizedError
from app.models.inbox import ChannelType, Inbox
from app.services import conversations as conversations_service
from app.services.inboxes import get_secrets

router = APIRouter()

_IGNORED_SUBTYPES = {"message_changed", "message_deleted", "bot_message"}


async def _find_slack_inbox(session: AsyncSession, team_id: str | None) -> Inbox | None:
    inboxes = (
        (
            await session.execute(
                select(Inbox)
                .where(Inbox.channel_type == ChannelType.SLACK, Inbox.enabled.is_(True))
                .order_by(Inbox.created_at)
            )
        )
        .scalars()
        .all()
    )
    if team_id:
        for inbox in inboxes:
            if inbox.config.get("team_id") == team_id:
                return inbox
    # Fall back to the workspace's single Slack inbox.
    return inboxes[0] if inboxes else None


@router.post("/events")
async def slack_events(request: Request, session: Db) -> dict[str, Any]:
    raw = await request.body()
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {"ok": True}

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    if payload.get("type") != "event_callback":
        return {"ok": True}

    inbox = await _find_slack_inbox(session, payload.get("team_id"))
    if inbox is None:
        raise NotFoundError("No Slack inbox configured")

    signing_secret = get_secrets(inbox).get("signing_secret", "")
    if not verify_signature(
        signing_secret,
        request.headers.get("X-Slack-Request-Timestamp"),
        raw,
        request.headers.get("X-Slack-Signature"),
    ):
        raise UnauthorizedError("Invalid Slack signature")

    event = payload.get("event") or {}
    if (
        event.get("type") != "message"
        or event.get("bot_id")
        or event.get("subtype") in _IGNORED_SUBTYPES
    ):
        return {"ok": True}

    channel = event.get("channel")
    ts = event.get("ts")
    thread_ts = event.get("thread_ts") or ts
    user = event.get("user") or ""
    await conversations_service.ingest_inbound(
        session,
        inbox,
        source_id=f"{channel}:{thread_ts}",
        content=event.get("text") or "",
        contact_info={"name": f"Slack user {user}", "external_id": f"slack:{user}"},
        message_source_id=event.get("client_msg_id") or f"{channel}:{ts}",
        meta={"slack_user": user},
    )
    return {"ok": True}
