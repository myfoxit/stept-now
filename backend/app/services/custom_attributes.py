"""Custom attribute definitions + value coercion/validation.

A definition describes one key inside a record's `attributes` JSON. `coerce`
turns whatever the API received into the stored representation and raises
`ValidationFailure` with the definition's own cue when it doesn't fit — so the
error a user sees is the one their admin wrote.

Values for keys with no definition pass through untouched: definitions are
additive metadata, not a schema lock. That keeps every existing integration
writing free-form attributes working exactly as before.

See docs/CHATWOOT-BACKLOG.md §1.6.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationFailure
from app.core.events import Actor
from app.models.custom_attribute import AttributeModel, AttributeType, CustomAttributeDefinition
from app.services import audit

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
# Operators the UI should offer per type — mirrors app.services.filters so the
# filter builder can render the right control without a second source of truth.
TYPE_OPERATORS: dict[str, tuple[str, ...]] = {
    AttributeType.TEXT.value: ("eq", "neq", "contains", "starts_with", "exists", "not_exists"),
    AttributeType.LINK.value: ("eq", "neq", "contains", "exists", "not_exists"),
    AttributeType.NUMBER.value: ("eq", "neq", "gt", "lt", "exists", "not_exists"),
    AttributeType.CURRENCY.value: ("eq", "neq", "gt", "lt", "exists", "not_exists"),
    AttributeType.PERCENT.value: ("eq", "neq", "gt", "lt", "exists", "not_exists"),
    AttributeType.DATE.value: ("eq", "neq", "gt", "lt", "exists", "not_exists"),
    AttributeType.LIST.value: ("eq", "neq", "in", "not_in", "exists", "not_exists"),
    AttributeType.CHECKBOX.value: ("eq", "neq", "exists", "not_exists"),
}


def _validate_key(key: str) -> str:
    key = key.strip().lower()
    if not _KEY_RE.match(key):
        raise ValidationFailure(
            "Attribute key must be lowercase letters, digits and underscores "
            "(starting with a letter or digit), max 64 characters"
        )
    return key


def coerce(definition: CustomAttributeDefinition, value: Any) -> Any:
    """Normalise `value` for storage, raising ValidationFailure when it doesn't
    match the definition. `None` clears the attribute and always passes."""
    if value is None:
        return None
    kind = definition.attribute_type
    label = definition.display_name

    if kind == AttributeType.CHECKBOX.value:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                return True
            if lowered in ("false", "0", "no", "off"):
                return False
        raise ValidationFailure(f"{label} must be true or false")

    if kind in (
        AttributeType.NUMBER.value,
        AttributeType.CURRENCY.value,
        AttributeType.PERCENT.value,
    ):
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationFailure(f"{label} must be a number") from exc
        if kind == AttributeType.PERCENT.value and not 0 <= number <= 100:
            raise ValidationFailure(f"{label} must be between 0 and 100")
        return int(number) if number.is_integer() else number

    if kind == AttributeType.DATE.value:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        try:
            return datetime.fromisoformat(str(value)).date().isoformat()
        except (TypeError, ValueError) as exc:
            raise ValidationFailure(f"{label} must be an ISO date") from exc

    if kind == AttributeType.LIST.value:
        options = [str(o) for o in (definition.options or [])]
        text = str(value)
        if options and text not in options:
            raise ValidationFailure(f"{label} must be one of: {', '.join(options)}")
        return text

    text = str(value)
    if kind == AttributeType.LINK.value and text and not re.match(r"^https?://", text):
        raise ValidationFailure(f"{label} must be a http(s) URL")
    if definition.regex_pattern:
        try:
            matches = re.match(definition.regex_pattern, text) is not None
        except re.error:
            matches = True  # a broken admin-supplied pattern must not block writes
        if not matches:
            raise ValidationFailure(definition.regex_cue or f"{label} has an invalid format")
    return text


async def definitions_for(
    session: AsyncSession, workspace_id: str, attribute_model: str
) -> list[CustomAttributeDefinition]:
    result = await session.execute(
        select(CustomAttributeDefinition)
        .where(
            CustomAttributeDefinition.workspace_id == workspace_id,
            CustomAttributeDefinition.attribute_model == attribute_model,
        )
        .order_by(CustomAttributeDefinition.ord, CustomAttributeDefinition.display_name)
    )
    return list(result.scalars())


async def validate_attributes(
    session: AsyncSession, workspace_id: str, attribute_model: str, attributes: dict[str, Any]
) -> dict[str, Any]:
    """Coerce every defined key in `attributes`; undefined keys pass through."""
    if not attributes:
        return attributes
    definitions = {d.key: d for d in await definitions_for(session, workspace_id, attribute_model)}
    if not definitions:
        return attributes
    out: dict[str, Any] = {}
    for key, value in attributes.items():
        definition = definitions.get(key)
        out[key] = coerce(definition, value) if definition is not None else value
    return out


async def apply_defaults(
    session: AsyncSession, workspace_id: str, attribute_model: str, attributes: dict[str, Any]
) -> dict[str, Any]:
    """Fill in `default_value` for definitions the payload didn't mention."""
    definitions = await definitions_for(session, workspace_id, attribute_model)
    out = dict(attributes or {})
    for definition in definitions:
        if definition.default_value is not None and definition.key not in out:
            out[definition.key] = definition.default_value
    return out


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def get_definition(
    session: AsyncSession, workspace_id: str, definition_id: str
) -> CustomAttributeDefinition:
    definition = await session.get(CustomAttributeDefinition, definition_id)
    if definition is None or definition.workspace_id != workspace_id:
        raise NotFoundError("Attribute definition not found")
    return definition


async def list_definitions(
    session: AsyncSession, workspace_id: str, *, attribute_model: str | None = None
) -> list[CustomAttributeDefinition]:
    query = select(CustomAttributeDefinition).where(
        CustomAttributeDefinition.workspace_id == workspace_id
    )
    if attribute_model is not None:
        query = query.where(CustomAttributeDefinition.attribute_model == attribute_model)
    query = query.order_by(
        CustomAttributeDefinition.attribute_model,
        CustomAttributeDefinition.ord,
        CustomAttributeDefinition.display_name,
    )
    return list((await session.execute(query)).scalars())


async def create_definition(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    attribute_model: str,
    key: str,
    display_name: str,
    attribute_type: str = AttributeType.TEXT.value,
    description: str | None = None,
    options: list[Any] | None = None,
    default_value: Any = None,
    regex_pattern: str | None = None,
    regex_cue: str | None = None,
    ord: int = 0,
    shown_on_front: bool = True,
) -> CustomAttributeDefinition:
    if attribute_model not in AttributeModel:
        raise ValidationFailure(f"Unknown attribute model {attribute_model!r}")
    if attribute_type not in AttributeType:
        raise ValidationFailure(f"Unknown attribute type {attribute_type!r}")
    key = _validate_key(key)
    if attribute_type == AttributeType.LIST.value and not options:
        raise ValidationFailure("A list attribute needs at least one option")
    if regex_pattern:
        try:
            re.compile(regex_pattern)
        except re.error as exc:
            raise ValidationFailure(f"Invalid regex pattern: {exc}") from exc

    existing = (
        await session.execute(
            select(CustomAttributeDefinition.id).where(
                CustomAttributeDefinition.workspace_id == workspace_id,
                CustomAttributeDefinition.attribute_model == attribute_model,
                CustomAttributeDefinition.key == key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(f"An attribute named {key!r} already exists on {attribute_model}")

    definition = CustomAttributeDefinition(
        workspace_id=workspace_id,
        attribute_model=attribute_model,
        key=key,
        display_name=display_name.strip(),
        description=description,
        attribute_type=attribute_type,
        options=list(options or []),
        default_value=default_value,
        regex_pattern=regex_pattern,
        regex_cue=regex_cue,
        ord=ord,
        shown_on_front=shown_on_front,
    )
    session.add(definition)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="custom_attribute.create",
        target_type="custom_attribute_definition",
        target_id=definition.id,
        meta={"key": key, "model": attribute_model},
    )
    return definition


async def update_definition(
    session: AsyncSession,
    workspace_id: str,
    definition_id: str,
    *,
    actor: Actor,
    changes: dict[str, Any],
) -> CustomAttributeDefinition:
    """The key and the model are immutable — changing either would orphan every
    stored value, which is a migration, not an edit."""
    definition = await get_definition(session, workspace_id, definition_id)
    if "attribute_type" in changes and changes["attribute_type"] not in AttributeType:
        raise ValidationFailure(f"Unknown attribute type {changes['attribute_type']!r}")
    if changes.get("regex_pattern"):
        try:
            re.compile(str(changes["regex_pattern"]))
        except re.error as exc:
            raise ValidationFailure(f"Invalid regex pattern: {exc}") from exc
    for field in (
        "display_name",
        "description",
        "attribute_type",
        "options",
        "default_value",
        "regex_pattern",
        "regex_cue",
        "ord",
        "shown_on_front",
    ):
        if field in changes:
            value = changes[field]
            setattr(
                definition, field, value.strip() if field == "display_name" and value else value
            )
    if definition.attribute_type == AttributeType.LIST.value and not definition.options:
        raise ValidationFailure("A list attribute needs at least one option")
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="custom_attribute.update",
        target_type="custom_attribute_definition",
        target_id=definition.id,
        meta={"key": definition.key},
    )
    return definition


async def delete_definition(
    session: AsyncSession, workspace_id: str, definition_id: str, *, actor: Actor
) -> None:
    """Removes the definition only — stored values stay put, so re-creating the
    definition brings the existing data back into view."""
    definition = await get_definition(session, workspace_id, definition_id)
    key, model = definition.key, definition.attribute_model
    await session.delete(definition)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="custom_attribute.delete",
        target_type="custom_attribute_definition",
        target_id=definition_id,
        meta={"key": key, "model": model},
    )
