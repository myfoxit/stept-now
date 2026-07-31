"""Segments: saved contact filters + the filter evaluation engine.

Core fields (email, name, external_id, last_seen_at, created_at, verified)
compile to SQL; `attributes.<key>` conditions are evaluated in Python after the
SQL candidate query so JSON filtering stays portable across Postgres and SQLite.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailure
from app.core.events import Actor
from app.models.contact import Contact
from app.models.segment import Segment
from app.services import audit

PostFilter = Callable[[Contact], bool]

_STRING_FIELDS = {"email": Contact.email, "name": Contact.name, "external_id": Contact.external_id}
_DATETIME_FIELDS = {"last_seen_at": Contact.last_seen_at, "created_at": Contact.created_at}
_ATTR_PREFIX = "attributes."


def _parse_datetime(field: str, value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationFailure(f"Filter on {field!r} needs an ISO datetime value") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _string_condition(field: str, op: str, value: Any) -> ColumnElement[bool]:
    column = _STRING_FIELDS[field]
    text = "" if value is None else str(value)
    if op == "eq":
        return column == text
    if op == "neq":
        return or_(column.is_(None), column != text)
    if op == "contains":
        return column.ilike(f"%{text}%")
    if op == "starts_with":
        return column.ilike(f"{text}%")
    if op == "exists":
        return and_(column.is_not(None), column != "")
    if op == "not_exists":
        return or_(column.is_(None), column == "")
    raise ValidationFailure(f"Operator {op!r} is not supported on {field!r}")


def _datetime_condition(field: str, op: str, value: Any) -> ColumnElement[bool]:
    column = _DATETIME_FIELDS[field]
    if op == "exists":
        return column.is_not(None)
    if op == "not_exists":
        return column.is_(None)
    moment = _parse_datetime(field, value)
    if op == "eq":
        return column == moment
    if op == "neq":
        return or_(column.is_(None), column != moment)
    if op == "gt":
        return column > moment
    if op == "lt":
        return column < moment
    raise ValidationFailure(f"Operator {op!r} is not supported on {field!r}")


def _verified_condition(op: str, value: Any) -> ColumnElement[bool]:
    if op == "eq":
        return Contact.verified == _as_bool(value)
    if op == "neq":
        return Contact.verified != _as_bool(value)
    raise ValidationFailure(f"Operator {op!r} is not supported on 'verified'")


def _attribute_filter(key: str, op: str, value: Any) -> PostFilter:
    def check(contact: Contact) -> bool:
        attrs = contact.attributes or {}
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
                return left > right if op == "gt" else left < right
            except (TypeError, ValueError):
                return str(current) > str(value) if op == "gt" else str(current) < str(value)
        return False

    return check


_OPS = {"eq", "neq", "contains", "starts_with", "exists", "not_exists", "gt", "lt"}


def compile_filters(
    filters: list[Any],
) -> tuple[list[ColumnElement[bool]], list[PostFilter]]:
    """Split filters into SQL conditions (core fields) + Python post-filters
    (`attributes.<key>`). Raises ValidationFailure on unknown fields/ops."""
    conditions: list[ColumnElement[bool]] = []
    post_filters: list[PostFilter] = []
    for raw in filters or []:
        item: dict[str, Any] = raw if isinstance(raw, dict) else raw.model_dump()
        field, op, value = item.get("field"), item.get("op"), item.get("value")
        if op not in _OPS:
            raise ValidationFailure(f"Unknown filter operator {op!r}")
        if not isinstance(field, str):
            raise ValidationFailure("Filter field must be a string")
        if field in _STRING_FIELDS:
            conditions.append(_string_condition(field, op, value))
        elif field in _DATETIME_FIELDS:
            conditions.append(_datetime_condition(field, op, value))
        elif field == "verified":
            conditions.append(_verified_condition(op, value))
        elif field.startswith(_ATTR_PREFIX) and len(field) > len(_ATTR_PREFIX):
            post_filters.append(_attribute_filter(field[len(_ATTR_PREFIX) :], op, value))
        else:
            raise ValidationFailure(f"Unknown filter field {field!r}")
    return conditions, post_filters


async def apply_filters(
    session: AsyncSession, workspace_id: str, filters: list[Any]
) -> list[Contact]:
    """All contacts in the workspace matching the filters (AND semantics),
    ordered like the directory: last_seen desc nulls-last, then created desc."""
    conditions, post_filters = compile_filters(filters)
    result = await session.execute(
        select(Contact)
        .where(Contact.workspace_id == workspace_id, *conditions)
        .order_by(
            Contact.last_seen_at.desc().nulls_last(),
            Contact.created_at.desc(),
            Contact.id.desc(),
        )
    )
    contacts = list(result.scalars())
    if not post_filters:
        return contacts
    return [c for c in contacts if all(check(c) for check in post_filters)]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def get_segment(session: AsyncSession, workspace_id: str, segment_id: str) -> Segment:
    segment = await session.get(Segment, segment_id)
    if segment is None or segment.workspace_id != workspace_id:
        raise NotFoundError("Segment not found")
    return segment


async def list_segments(session: AsyncSession, workspace_id: str) -> list[Segment]:
    result = await session.execute(
        select(Segment).where(Segment.workspace_id == workspace_id).order_by(Segment.name)
    )
    return list(result.scalars())


async def create_segment(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    name: str,
    filters: list[dict[str, Any]],
) -> Segment:
    compile_filters(filters)  # validate before persisting
    segment = Segment(
        workspace_id=workspace_id,
        name=name.strip(),
        filters=filters,
        created_by=actor.id if actor.type == "user" else None,
    )
    session.add(segment)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="segment.create",
        target_type="segment",
        target_id=segment.id,
        meta={"name": segment.name},
    )
    return segment


async def update_segment(
    session: AsyncSession,
    workspace_id: str,
    segment_id: str,
    *,
    actor: Actor,
    name: str | None = None,
    filters: list[dict[str, Any]] | None = None,
) -> Segment:
    segment = await get_segment(session, workspace_id, segment_id)
    if name is not None:
        segment.name = name.strip()
    if filters is not None:
        compile_filters(filters)
        segment.filters = filters
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="segment.update",
        target_type="segment",
        target_id=segment.id,
        meta={"name": segment.name},
    )
    return segment


async def delete_segment(
    session: AsyncSession, workspace_id: str, segment_id: str, *, actor: Actor
) -> None:
    segment = await get_segment(session, workspace_id, segment_id)
    await session.delete(segment)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="segment.delete",
        target_type="segment",
        target_id=segment_id,
        meta={"name": segment.name},
    )
