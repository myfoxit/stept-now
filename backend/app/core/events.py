"""In-process domain event bus.

Services emit events after flushing (ids exist, still inside the request
transaction). Handlers run inline and MUST be fast; anything heavy enqueues a
background task via `app.core.queue`. Handler failures are logged, never
propagated — a broken webhook must not break message delivery.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log

logger = log("events")


@dataclass(frozen=True)
class Actor:
    type: str  # "user" | "contact" | "api_key" | "agent" | "system"
    id: str | None = None
    label: str | None = None

    @classmethod
    def system(cls) -> Actor:
        return cls(type="system")


@dataclass(frozen=True)
class Event:
    name: str
    workspace_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor: Actor = field(default_factory=Actor.system)


class EventNames:
    WORKSPACE_CREATED = "workspace.created"
    MEMBER_JOINED = "member.joined"
    CONTACT_CREATED = "contact.created"
    CONVERSATION_CREATED = "conversation.created"
    CONVERSATION_UPDATED = "conversation.updated"
    CONVERSATION_ASSIGNED = "conversation.assigned"
    CONVERSATION_STATUS_CHANGED = "conversation.status_changed"
    MESSAGE_CREATED = "message.created"
    CSAT_SUBMITTED = "csat.submitted"
    DOCUMENT_INDEXED = "document.indexed"
    AGENT_RUN_STARTED = "agent_run.started"
    AGENT_RUN_COMPLETED = "agent_run.completed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    TOUR_EVENT = "tour.event"


Handler = Callable[[AsyncSession, Event], Awaitable[None]]

_subscribers: dict[str, list[Handler]] = {}


def on(*names: str) -> Callable[[Handler], Handler]:
    """Subscribe a handler to event names ("*" for all)."""

    def decorator(fn: Handler) -> Handler:
        for name in names:
            _subscribers.setdefault(name, []).append(fn)
        return fn

    return decorator


async def emit(session: AsyncSession, event: Event) -> None:
    handlers = [*_subscribers.get(event.name, []), *_subscribers.get("*", [])]
    for handler in handlers:
        try:
            await handler(session, event)
        except Exception:
            logger.exception("event handler %s failed for %s", handler.__qualname__, event.name)


def clear_subscribers() -> None:
    """Test helper."""
    _subscribers.clear()
