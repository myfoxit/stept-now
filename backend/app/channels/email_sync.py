"""IMAP polling for email inboxes (W11).

Every minute the ``email_imap_scan`` scheduler job enqueues an
``email_imap_poll`` task for each enabled email inbox whose ``imap.enabled``
config is on and whose ``poll_minutes`` cadence (min 2, default 3) has elapsed.
The poll runs stdlib :mod:`imaplib` inside ``asyncio.to_thread``: UID SEARCH
from the stored ``imap_uid_cursor`` (inbox config, advanced after each poll),
FETCH RFC822, parse with stdlib :mod:`email`, and route through the same
:func:`app.channels.email.process_inbound` pipeline as the webhooks.

The first poll of a mailbox is a baseline: it records the current max UID and
imports nothing, so enabling IMAP on a busy mailbox does not flood the
workspace with historical mail.

Auth: LOGIN with ``imap.username`` + the ``imap_password`` secret, or XOAUTH2
for gmail/microsoft transports with a live token from the integrations tokens
seam. Auth failures set ``config.reauth_required = true`` and emit
``integration.reauth_required`` (UI banner); they never hot-loop the task.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.email import parse_mime_email, process_inbound
from app.channels.email_transports import OAUTH_IMAP_HOSTS, IntegrationAuthError
from app.core.db import session_scope, utcnow
from app.core.errors import BlockedContactError
from app.core.events import Event, EventNames, emit
from app.core.logging import log
from app.core.queue import TaskContext, enqueue, task
from app.core.scheduler import scheduled
from app.integrations import tokens as integration_tokens
from app.models.inbox import ChannelType, Inbox
from app.services.inboxes import get_secrets

logger = log("email_sync")

POLL_TASK = "email_imap_poll"
MIN_POLL_MINUTES = 2
DEFAULT_POLL_MINUTES = 3


class ImapAuthError(RuntimeError):
    """IMAP rejected our credentials (as opposed to a transport error)."""


# ---------------------------------------------------------------------------
# blocking IMAP leg (runs in a thread)
# ---------------------------------------------------------------------------


def _parse_uids(data: list[Any]) -> list[int]:
    if not data or not data[0]:
        return []
    raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    return [int(part) for part in raw.split() if part.isdigit()]


def _fetch_payload(fetched: list[Any]) -> bytes | None:
    for item in fetched or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _poll_blocking(
    host: str,
    port: int,
    *,
    login: str,
    password: str | None,
    token: str | None,
    cursor: int | None,
    mark_seen: bool,
) -> tuple[int, list[bytes]]:
    """Connect, authenticate, and fetch every UID above the cursor.

    Returns ``(new_cursor, raw_messages)``. A ``cursor`` of None is the
    baseline run: record the mailbox's max UID, fetch nothing.
    """
    client = imaplib.IMAP4_SSL(host, port)
    try:
        try:
            if token is not None:
                auth_bytes = f"user={login}\x01auth=Bearer {token}\x01\x01".encode()
                client.authenticate("XOAUTH2", lambda _challenge: auth_bytes)
            else:
                client.login(login, password or "")
        except imaplib.IMAP4.error as exc:
            raise ImapAuthError(str(exc)) from exc

        client.select("INBOX", readonly=not mark_seen)
        if cursor is None:
            _status, data = client.uid("search", "ALL")
            uids = _parse_uids(data)
            return (max(uids) if uids else 0, [])

        _status, data = client.uid("search", f"UID {cursor + 1}:*")
        # "{n}:*" always matches the newest message even when its UID < n.
        uids = sorted(uid for uid in _parse_uids(data) if uid > cursor)
        raws: list[bytes] = []
        for uid in uids:
            _status, fetched = client.uid("fetch", str(uid), "(RFC822)")
            raw = _fetch_payload(fetched)
            if raw is not None:
                raws.append(raw)
            if mark_seen:
                client.uid("store", str(uid), "+FLAGS", "(\\Seen)")
        return (uids[-1] if uids else cursor, raws)
    finally:
        with contextlib.suppress(Exception):
            client.logout()


# ---------------------------------------------------------------------------
# async orchestration
# ---------------------------------------------------------------------------


async def _flag_reauth(session: AsyncSession, inbox: Inbox, reason: str) -> None:
    logger.warning("imap auth failed for inbox %s: %s", inbox.id, reason[:200])
    if inbox.config.get("reauth_required"):
        return
    inbox.config = {**inbox.config, "reauth_required": True}
    await emit(
        session,
        Event(
            name=EventNames.INTEGRATION_REAUTH_REQUIRED,
            workspace_id=inbox.workspace_id,
            payload={
                "inbox_id": inbox.id,
                "transport": inbox.config.get("transport") or "global",
                "source": "email_imap",
            },
        ),
    )


async def poll_inbox(session: AsyncSession, inbox: Inbox) -> int:
    """Poll one inbox's IMAP mailbox; returns how many messages were ingested."""
    config = inbox.config or {}
    imap_cfg = config.get("imap") or {}
    if not imap_cfg.get("enabled"):
        return 0
    transport = config.get("transport") or "global"
    host = imap_cfg.get("host") or OAUTH_IMAP_HOSTS.get(transport)
    port = int(imap_cfg.get("port") or 993)
    login = imap_cfg.get("username") or None
    password: str | None = None
    token: str | None = None

    try:
        if transport in OAUTH_IMAP_HOSTS:
            connection_id = config.get("connection_id")
            if not connection_id:
                logger.warning("imap poll: inbox %s has no connection_id", inbox.id)
                return 0
            connection = await integration_tokens.get_connection(  # type: ignore[attr-defined]
                session, inbox.workspace_id, connection_id
            )
            token = await integration_tokens.get_valid_access_token(  # type: ignore[attr-defined]
                session, connection
            )
            login = login or connection.account_label
        else:
            password = get_secrets(inbox).get("imap_password") or None
    except IntegrationAuthError as exc:
        await _flag_reauth(session, inbox, str(exc))
        return 0

    if not host or not login or (token is None and password is None):
        logger.warning("imap poll: inbox %s is missing host or credentials", inbox.id)
        return 0

    raw_cursor = config.get("imap_uid_cursor")
    cursor = int(raw_cursor) if raw_cursor is not None else None
    try:
        new_cursor, raws = await asyncio.to_thread(
            _poll_blocking,
            str(host),
            port,
            login=str(login),
            password=password,
            token=token,
            cursor=cursor,
            mark_seen=bool(imap_cfg.get("mark_seen")),
        )
    except ImapAuthError as exc:
        await _flag_reauth(session, inbox, str(exc))
        return 0

    imported = 0
    for raw in raws:
        inbound = parse_mime_email(raw)
        try:
            result = await process_inbound(session, inbound, inbox=inbox)
        except BlockedContactError:
            continue  # dropped at the channel door, poll goes on
        if result.get("status") in ("created", "appended"):
            imported += 1

    updated = {key: value for key, value in inbox.config.items() if key != "reauth_required"}
    updated["imap_uid_cursor"] = new_cursor
    updated["imap_last_poll_at"] = utcnow().isoformat()
    inbox.config = updated
    return imported


# ---------------------------------------------------------------------------
# scheduler + task
# ---------------------------------------------------------------------------


def _poll_minutes(imap_cfg: dict[str, Any]) -> int:
    try:
        minutes = int(imap_cfg.get("poll_minutes") or DEFAULT_POLL_MINUTES)
    except (TypeError, ValueError):
        minutes = DEFAULT_POLL_MINUTES
    return max(minutes, MIN_POLL_MINUTES)


def _due(config: dict[str, Any], now: datetime) -> bool:
    imap_cfg = config.get("imap") or {}
    if not imap_cfg.get("enabled"):
        return False
    last_raw = config.get("imap_last_poll_at")
    if not last_raw:
        return True
    try:
        last = datetime.fromisoformat(str(last_raw))
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return now - last >= timedelta(minutes=_poll_minutes(imap_cfg))


async def scan_imap_inboxes() -> list[str]:
    """Enqueue polls for due inboxes; stamps ``imap_last_poll_at`` at enqueue
    so a slow poll is not enqueued again on the next tick."""
    now = utcnow()
    due: list[str] = []
    async with session_scope() as session:
        inboxes = (
            (
                await session.execute(
                    select(Inbox).where(
                        Inbox.channel_type == ChannelType.EMAIL, Inbox.enabled.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        for inbox in inboxes:
            if _due(inbox.config or {}, now):
                inbox.config = {**inbox.config, "imap_last_poll_at": now.isoformat()}
                due.append(inbox.id)
    for inbox_id in due:
        await enqueue(POLL_TASK, inbox_id=inbox_id)
    return due


@scheduled("email_imap_scan", every_seconds=60)
async def _email_imap_scan() -> None:
    await scan_imap_inboxes()


@task(POLL_TASK)
async def email_imap_poll(ctx: TaskContext, *, inbox_id: str, **_: Any) -> None:
    async with session_scope() as session:
        inbox = await session.get(Inbox, inbox_id)
        if inbox is None or inbox.channel_type != ChannelType.EMAIL or not inbox.enabled:
            return
        await poll_inbox(session, inbox)
