"""Channel sender registry + the deliver_message background task.

Conversation logic is 100% channel-agnostic; only delivery dispatches per
channel. Wave-2 channel adapters register senders with @register_sender —
until then (or for unknown channels) delivery marks the message failed.

A sender is `async def sender(session, inbox, message) -> None`; raising marks
the message failed with the error text.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope
from app.core.logging import log
from app.core.queue import TaskContext, task
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.message import DeliveryStatus, Message

logger = log("channels")

Sender = Callable[[AsyncSession, Inbox, Message], Awaitable[None]]

# channel_type → async sender(session, inbox, message)
SENDERS: dict[str, Sender] = {}


def register_sender(channel_type: str) -> Callable[[Sender], Sender]:
    """Register the outbound sender for a channel type."""

    def decorator(fn: Sender) -> Sender:
        SENDERS[channel_type] = fn
        return fn

    return decorator


@task("deliver_message")
async def deliver_message(ctx: TaskContext, *, message_id: str, **_: Any) -> None:
    """Deliver an outbound message through its inbox's channel sender.

    Raises when the rows aren't visible yet (enqueued mid-transaction) so the
    queue's retry/backoff picks it up after commit — tasks are at-least-once.
    """
    async with session_scope() as session:
        message = await session.get(Message, message_id)
        if message is None:
            raise RuntimeError(f"deliver_message: message {message_id} not found (yet)")
        if message.delivery_status == DeliveryStatus.SENT:
            return  # already delivered (retry after partial failure)
        conversation = await session.get(Conversation, message.conversation_id)
        inbox = (
            await session.get(Inbox, conversation.inbox_id) if conversation is not None else None
        )
        if inbox is None:
            message.delivery_status = DeliveryStatus.FAILED
            message.delivery_error = "inbox not found"
            return
        sender = SENDERS.get(inbox.channel_type)
        if sender is None:
            message.delivery_status = DeliveryStatus.FAILED
            message.delivery_error = "no sender registered"
            return
        try:
            await sender(session, inbox, message)
        except Exception as exc:  # noqa: BLE001 — sender errors become delivery_error
            logger.warning(
                "delivery via %s failed for message %s: %s", inbox.channel_type, message_id, exc
            )
            message.delivery_status = DeliveryStatus.FAILED
            message.delivery_error = str(exc)[:500]
            return
        message.delivery_status = DeliveryStatus.SENT
        message.delivery_error = None
