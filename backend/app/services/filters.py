"""Conversation filter DSL — one engine, four consumers.

A filter document is::

    {"match": "all" | "any",
     "conditions": [{"field": "status", "op": "in", "value": ["open"]}, …]}

`compile_conversation_filter` returns SQL conditions plus Python post-filters
(for `attributes.<key>`, which stays out of SQL so the same query runs on
Postgres and SQLite). Consumers: the conversation list, saved views
(§1.3), bulk actions (§1.4) and report drill-down (§1.5).

Field/operator pairs are validated eagerly so a saved view can never persist a
document that later explodes at read time.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, false, not_, or_, select, true

from app.core.errors import ValidationFailure
from app.models.collaboration import ConversationParticipant
from app.models.contact import Contact
from app.models.conversation import Conversation, ConversationTag
from app.models.custom_attribute import AttributeModel

PostFilter = Callable[[Conversation], bool]

MATCH_MODES = ("all", "any")
_ATTR_PREFIX = "attributes."

# field -> the operators it accepts. Keeping this explicit (rather than deriving
# it) is what lets the frontend render the right control per field.
FIELD_OPS: dict[str, tuple[str, ...]] = {
    "status": ("eq", "neq", "in", "not_in"),
    "priority": ("eq", "neq", "in", "not_in"),
    "inbox_id": ("eq", "neq", "in", "not_in"),
    "team_id": ("eq", "neq", "in", "not_in", "exists", "not_exists"),
    "assignee_user_id": ("eq", "neq", "in", "not_in", "exists", "not_exists"),
    "ai_agent_id": ("exists", "not_exists"),
    "contact_id": ("eq", "neq", "in", "not_in"),
    "subject": ("contains", "starts_with", "eq", "exists", "not_exists"),
    "tag_id": ("in", "not_in", "exists", "not_exists"),
    "participant_user_id": ("in", "not_in"),
    "contact_email": ("eq", "neq", "contains", "exists", "not_exists"),
    "contact_name": ("eq", "neq", "contains", "starts_with"),
    "created_at": ("gt", "lt", "within_days", "before_days"),
    "last_activity_at": ("gt", "lt", "within_days", "before_days"),
    "resolved_at": ("gt", "lt", "exists", "not_exists"),
    "first_reply_at": ("gt", "lt", "exists", "not_exists"),
    "waiting_since": ("gt", "lt", "exists", "not_exists"),
    "snoozed_until": ("gt", "lt", "exists", "not_exists"),
}

_SCALAR_COLUMNS = {
    "status": Conversation.status,
    "priority": Conversation.priority,
    "inbox_id": Conversation.inbox_id,
    "team_id": Conversation.team_id,
    "assignee_user_id": Conversation.assignee_user_id,
    "contact_id": Conversation.contact_id,
    "subject": Conversation.subject,
}
_DATE_COLUMNS = {
    "created_at": Conversation.created_at,
    "last_activity_at": Conversation.last_activity_at,
    "resolved_at": Conversation.resolved_at,
    "first_reply_at": Conversation.first_reply_at,
    "waiting_since": Conversation.waiting_since,
    "snoozed_until": Conversation.snoozed_until,
}
_NULLABLE_COLUMNS = {
    "ai_agent_id": Conversation.ai_agent_id,
    "team_id": Conversation.team_id,
    "assignee_user_id": Conversation.assignee_user_id,
}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list | tuple | set):
        return [v for v in value]
    return [value]


def _parse_dt(field: str, value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationFailure(f"Filter on {field!r} needs an ISO datetime value") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _days(field: str, value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationFailure(f"Filter on {field!r} needs a number of days") from exc
    if days < 0:
        raise ValidationFailure(f"Filter on {field!r} needs a non-negative number of days")
    return days


def _scalar_condition(field: str, op: str, value: Any) -> ColumnElement[bool]:
    column = _SCALAR_COLUMNS[field]
    if op == "eq":
        return column == value
    if op == "neq":
        return or_(column.is_(None), column != value)
    if op == "in":
        # An empty set matches nothing; an empty "not in" excludes nothing.
        values = _as_list(value)
        return column.in_(values) if values else false()
    if op == "not_in":
        values = _as_list(value)
        return or_(column.is_(None), column.not_in(values)) if values else true()
    if op == "contains":
        return column.ilike(f"%{value}%")
    if op == "starts_with":
        return column.ilike(f"{value}%")
    if op == "exists":
        return and_(column.is_not(None), column != "")
    if op == "not_exists":
        return or_(column.is_(None), column == "")
    raise ValidationFailure(f"Operator {op!r} is not supported on {field!r}")


def _date_condition(field: str, op: str, value: Any, now: datetime) -> ColumnElement[bool]:
    column = _DATE_COLUMNS[field]
    if op == "exists":
        return column.is_not(None)
    if op == "not_exists":
        return column.is_(None)
    if op == "within_days":
        return column >= now - timedelta(days=_days(field, value))
    if op == "before_days":
        return column < now - timedelta(days=_days(field, value))
    moment = _parse_dt(field, value)
    if op == "gt":
        return column > moment
    if op == "lt":
        return column < moment
    raise ValidationFailure(f"Operator {op!r} is not supported on {field!r}")


def _tag_condition(op: str, value: Any) -> ColumnElement[bool]:
    has_any = exists(
        select(ConversationTag.id).where(
            ConversationTag.conversation_id == Conversation.id,
            *([ConversationTag.tag_id.in_(_as_list(value))] if op in ("in", "not_in") else []),
        )
    )
    if op in ("in", "exists"):
        return has_any
    return not_(has_any)  # not_in | not_exists


def _participant_condition(op: str, value: Any) -> ColumnElement[bool]:
    has_any = exists(
        select(ConversationParticipant.id).where(
            ConversationParticipant.conversation_id == Conversation.id,
            ConversationParticipant.user_id.in_(_as_list(value)),
        )
    )
    return has_any if op == "in" else not_(has_any)


def _contact_condition(field: str, op: str, value: Any) -> ColumnElement[bool]:
    column = Contact.email if field == "contact_email" else Contact.name
    if op == "eq":
        inner = column == value
    elif op == "neq":
        inner = or_(column.is_(None), column != value)
    elif op == "contains":
        inner = column.ilike(f"%{value}%")
    elif op == "starts_with":
        inner = column.ilike(f"{value}%")
    elif op == "exists":
        inner = and_(column.is_not(None), column != "")
    elif op == "not_exists":
        inner = or_(column.is_(None), column == "")
    else:
        raise ValidationFailure(f"Operator {op!r} is not supported on {field!r}")
    return exists(select(Contact.id).where(Contact.id == Conversation.contact_id, inner))


def _attribute_filter(key: str, op: str, value: Any) -> PostFilter:
    def check(conversation: Conversation) -> bool:
        attrs = conversation.attributes or {}
        present = key in attrs and attrs[key] is not None
        current = attrs.get(key)
        if op == "exists":
            return present
        if op == "not_exists":
            return not present
        if op == "eq":
            return present and current == value
        if op == "neq":
            return not present or current != value
        if op == "in":
            return present and current in _as_list(value)
        if op == "not_in":
            return not present or current not in _as_list(value)
        if not present:
            return False
        if op == "contains":
            if isinstance(current, list | tuple):
                return value in current
            return str(value).lower() in str(current).lower()
        if op == "starts_with":
            return str(current).lower().startswith(str(value).lower())
        if op in ("gt", "lt"):
            try:
                left, right = float(str(current)), float(str(value))
            except (TypeError, ValueError):
                left_s, right_s = str(current), str(value)
                return left_s > right_s if op == "gt" else left_s < right_s
            return left > right if op == "gt" else left < right
        raise ValidationFailure(f"Operator {op!r} is not supported on attributes")

    return check


_ATTR_OPS = (
    "eq",
    "neq",
    "in",
    "not_in",
    "contains",
    "starts_with",
    "exists",
    "not_exists",
    "gt",
    "lt",
)


def _needs_value(op: str) -> bool:
    return op not in ("exists", "not_exists")


def compile_conversation_filter(
    query: dict[str, Any] | None, *, now: datetime | None = None
) -> tuple[ColumnElement[bool] | None, list[PostFilter], str]:
    """Compile a filter document.

    Returns `(sql_condition | None, post_filters, match_mode)`.

    `attributes.<key>` conditions are evaluated in Python (JSON filtering isn't
    portable across Postgres and SQLite), so they can only narrow a SQL result
    set — never widen it. Under `match: "any"` a row could match on the
    attribute alone, which would need a full-table scan to find, so that
    combination is rejected up front rather than silently returning too few
    rows.
    """
    now = now or datetime.now(UTC)
    if not query:
        return None, [], "all"
    match = str(query.get("match", "all")).lower()
    if match not in MATCH_MODES:
        raise ValidationFailure(f"match must be one of {MATCH_MODES}")
    raw_conditions = query.get("conditions") or []
    if not isinstance(raw_conditions, list):
        raise ValidationFailure("conditions must be a list")

    sql_parts: list[ColumnElement[bool]] = []
    post_filters: list[PostFilter] = []
    for raw in raw_conditions:
        item: dict[str, Any] = raw if isinstance(raw, dict) else raw.model_dump()
        field, op, value = item.get("field"), item.get("op"), item.get("value")
        if not isinstance(field, str) or not isinstance(op, str):
            raise ValidationFailure("Each condition needs a string field and op")

        if field.startswith(_ATTR_PREFIX) and len(field) > len(_ATTR_PREFIX):
            if op not in _ATTR_OPS:
                raise ValidationFailure(f"Operator {op!r} is not supported on {field!r}")
            post_filters.append(_attribute_filter(field[len(_ATTR_PREFIX) :], op, value))
            continue

        allowed = FIELD_OPS.get(field)
        if allowed is None:
            raise ValidationFailure(f"Unknown filter field {field!r}")
        if op not in allowed:
            raise ValidationFailure(
                f"Operator {op!r} is not supported on {field!r} (allowed: {', '.join(allowed)})"
            )
        if _needs_value(op) and value is None:
            raise ValidationFailure(f"Filter on {field!r} with {op!r} needs a value")

        if field == "tag_id":
            sql_parts.append(_tag_condition(op, value))
        elif field == "participant_user_id":
            sql_parts.append(_participant_condition(op, value))
        elif field in ("contact_email", "contact_name"):
            sql_parts.append(_contact_condition(field, op, value))
        elif field in _DATE_COLUMNS:
            sql_parts.append(_date_condition(field, op, value, now))
        elif field in _SCALAR_COLUMNS:
            sql_parts.append(_scalar_condition(field, op, value))
        elif field in _NULLABLE_COLUMNS:
            column = _NULLABLE_COLUMNS[field]
            sql_parts.append(column.is_not(None) if op == "exists" else column.is_(None))
        else:  # pragma: no cover - FIELD_OPS and the branches above stay in sync
            raise ValidationFailure(f"Unknown filter field {field!r}")

    if post_filters and match == "any":
        raise ValidationFailure(
            "attributes.* conditions require match='all' "
            "(they cannot be combined with 'any' without scanning every conversation)"
        )
    if not sql_parts:
        return None, post_filters, match
    combined = and_(*sql_parts) if match == "all" else or_(*sql_parts)
    return combined, post_filters, match


def validate_conversation_filter(query: dict[str, Any] | None) -> None:
    """Raise ValidationFailure if the document is malformed (used before persisting)."""
    compile_conversation_filter(query)


def passes_post_filters(conversation: Conversation, post_filters: list[PostFilter]) -> bool:
    """Post-filters only ever narrow (see `compile_conversation_filter`), so they
    are always AND-ed."""
    return all(check(conversation) for check in post_filters)


def attribute_model_for(kind: str) -> str:
    return AttributeModel.CONTACT.value if kind == "contact" else AttributeModel.CONVERSATION.value
