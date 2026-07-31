"""Condition evaluation for automation rules.

Conditions are ANDed. Each is ``{"field", "op", "value"}`` where field is one of
the contract's selectors (inbox_id, channel_type, status, priority,
subject_contains, content_contains, contact.email, contact.attributes.<k>, tag)
and op is one of eq | neq | contains | in | exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.events import Event
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message


@dataclass
class RuleContext:
    """Hydrated subjects a rule's conditions/actions read from."""

    event: Event
    conversation: Conversation | None
    contact: Contact | None
    message: Message | None
    channel_type: str | None
    tag_ids: list[str]


_ATTR_PREFIX = "contact.attributes."


def field_value(field: str, ctx: RuleContext) -> Any:
    """Resolve a condition field to its current value (or None / [] when absent)."""
    conversation = ctx.conversation
    contact = ctx.contact
    message = ctx.message
    if field == "inbox_id":
        return conversation.inbox_id if conversation else None
    if field == "channel_type":
        return ctx.channel_type
    if field == "status":
        return conversation.status if conversation else None
    if field == "priority":
        return conversation.priority if conversation else None
    if field in ("subject", "subject_contains"):
        return conversation.subject if conversation else None
    if field in ("content", "content_contains"):
        return message.content if message else None
    if field == "contact.email":
        return contact.email if contact else None
    if field == "tag":
        return ctx.tag_ids
    if field.startswith(_ATTR_PREFIX):
        key = field[len(_ATTR_PREFIX) :]
        return (contact.attributes or {}).get(key) if contact else None
    return None


def _scalar_eq(actual: Any, expected: Any) -> bool:
    if actual == expected:
        return True
    if actual is None or expected is None:
        return False
    return str(actual) == str(expected)


def apply_op(op: str, actual: Any, expected: Any) -> bool:
    if op == "exists":
        if isinstance(actual, list):
            return len(actual) > 0
        return actual is not None and actual != ""
    if op == "eq":
        if isinstance(actual, list):
            return expected in actual
        return _scalar_eq(actual, expected)
    if op == "neq":
        if isinstance(actual, list):
            return expected not in actual
        return not _scalar_eq(actual, expected)
    if op == "contains":
        if isinstance(actual, list):
            return expected in actual
        if actual is None:
            return False
        return str(expected).lower() in str(actual).lower()
    if op == "in":
        options = expected if isinstance(expected, list) else [expected]
        if isinstance(actual, list):
            return any(item in options for item in actual)
        return actual in options
    return False


def _match_one(condition: dict[str, Any], ctx: RuleContext) -> bool:
    field = condition.get("field", "")
    op = condition.get("op", "eq")
    expected = condition.get("value")
    return apply_op(op, field_value(field, ctx), expected)


def matches(conditions: list[dict[str, Any]], ctx: RuleContext) -> bool:
    """True when every condition holds (an empty list matches everything)."""
    return all(_match_one(condition, ctx) for condition in conditions)
