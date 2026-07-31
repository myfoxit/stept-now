"""Automation rules engine — Wave 2 agent F (see docs/CONTRACTS.md).

Two subscribers register at import time:

* ``run_rules`` (@on the five rule-triggering events) hydrates a context from the
  event's ids, evaluates each enabled rule's AND-ed conditions in ``ord`` order,
  and runs its actions.
* ``fan_out_webhooks`` (@on "*") mirrors every domain event to matching outbound
  webhooks.

This module is imported for its side effects at the top of
``app/api/v1/automations.py`` and ``app/api/v1/webhooks.py`` so the handlers are
live whenever the app (or a test app) is built.

Loop prevention on ``message.created``: system/automation-authored messages and
activity entries are ignored, and each (rule, message) pair runs at most once
(tracked in ``message.meta["automation_handled"]``).
"""

from __future__ import annotations

import contextvars

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation import actions as action_runner
from app.automation.conditions import RuleContext, matches
from app.core.events import Event, EventNames, on
from app.core.logging import log
from app.models.automation import AutomationRule
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.message import AuthorType, Message, MessageVisibility
from app.services import conversations as conversations_service
from app.services import webhooks as webhooks_service

logger = log("automation")

RULE_EVENTS = (
    EventNames.CONVERSATION_CREATED,
    EventNames.MESSAGE_CREATED,
    EventNames.CONVERSATION_STATUS_CHANGED,
    EventNames.CSAT_SUBMITTED,
    EventNames.CONTACT_CREATED,
)

# Bounds cascades (a rule action re-emitting a subscribed event) so a
# misconfigured pair of rules can't recurse without limit.
MAX_DEPTH = 8
_depth: contextvars.ContextVar[int] = contextvars.ContextVar("automation_depth", default=0)

_HANDLED_KEY = "automation_handled"


# ---------------------------------------------------------------------------
# rule handler
# ---------------------------------------------------------------------------


@on(*RULE_EVENTS)
async def run_rules(session: AsyncSession, event: Event) -> None:
    if _depth.get() >= MAX_DEPTH:
        logger.warning("automation depth cap reached; skipping %s", event.name)
        return

    message = await _guarded_message(session, event)
    if event.name == EventNames.MESSAGE_CREATED and message is None:
        return

    rules = (
        (
            await session.execute(
                select(AutomationRule)
                .where(
                    AutomationRule.workspace_id == event.workspace_id,
                    AutomationRule.event == event.name,
                    AutomationRule.enabled.is_(True),
                )
                .order_by(AutomationRule.ord, AutomationRule.created_at, AutomationRule.id)
            )
        )
        .scalars()
        .all()
    )
    if not rules:
        return

    ctx = await _hydrate(session, event, message)
    token = _depth.set(_depth.get() + 1)
    try:
        for rule in rules:
            if message is not None and _already_handled(message, rule.id):
                continue
            try:
                if matches(list(rule.conditions), ctx):
                    for action in rule.actions:
                        await action_runner.run_action(
                            session, action.get("type", ""), action.get("params") or {}, ctx
                        )
                    if message is not None:
                        _mark_handled(message, rule.id)
            except Exception:
                logger.exception("automation rule %s failed on %s", rule.id, event.name)
    finally:
        _depth.reset(token)


async def _guarded_message(session: AsyncSession, event: Event) -> Message | None:
    """For message.created, load the message unless it must be skipped."""
    if event.name != EventNames.MESSAGE_CREATED:
        return None
    if event.payload.get("author_type") == AuthorType.SYSTEM.value:
        return None  # never react to system/automation-authored messages (loop prevention)
    message_id = event.payload.get("message_id")
    if not message_id:
        return None
    message = await session.get(Message, message_id)
    if message is None or message.visibility == MessageVisibility.ACTIVITY.value:
        return None
    return message


def _already_handled(message: Message, rule_id: str) -> bool:
    return rule_id in (message.meta.get(_HANDLED_KEY) or [])


def _mark_handled(message: Message, rule_id: str) -> None:
    handled = list(message.meta.get(_HANDLED_KEY) or [])
    if rule_id not in handled:
        handled.append(rule_id)
        # PortableJSON isn't mutation-tracked — reassign to mark the row dirty.
        message.meta = {**message.meta, _HANDLED_KEY: handled}


async def _hydrate(session: AsyncSession, event: Event, message: Message | None) -> RuleContext:
    payload = event.payload
    conversation: Conversation | None = None
    contact: Contact | None = None
    channel_type: str | None = None
    tag_ids: list[str] = []

    conversation_id = payload.get("conversation_id")
    if message is not None and conversation_id is None:
        conversation_id = message.conversation_id
    if conversation_id:
        conversation = await session.get(Conversation, conversation_id)

    if conversation is not None:
        contact = await session.get(Contact, conversation.contact_id)
        inbox = await session.get(Inbox, conversation.inbox_id)
        channel_type = inbox.channel_type if inbox is not None else None
        tag_ids = await conversations_service.tag_ids_for(session, conversation.id)

    if contact is None and payload.get("contact_id"):
        contact = await session.get(Contact, payload["contact_id"])

    return RuleContext(
        event=event,
        conversation=conversation,
        contact=contact,
        message=message,
        channel_type=channel_type,
        tag_ids=tag_ids,
    )


# ---------------------------------------------------------------------------
# webhook fan-out
# ---------------------------------------------------------------------------


@on("*")
async def fan_out_webhooks(session: AsyncSession, event: Event) -> None:
    # Skip with zero DB work for workspaces confirmed to have no enabled webhooks
    # (the common case); fan_out self-seeds and refreshes that cache.
    if webhooks_service.known_no_webhooks(event.workspace_id):
        return
    await webhooks_service.fan_out(session, event)
