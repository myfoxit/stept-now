"""Service layer for macros.

Macros run the automation action vocabulary on demand, attributed to the member
who runs them (Chatwoot §4): actions execute sequentially through the
conversation services; per-action failures are collected and execution
continues. `{{contact.name}}` / `{{agent.name}}` placeholders in send_reply /
send_note content are substituted like canned responses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailure
from app.core.events import Actor
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.macro import Macro, MacroVisibility
from app.models.message import AuthorType, MessageDirection, MessageVisibility
from app.models.tag import Tag
from app.models.user import User
from app.services import audit
from app.services import conversations as conversations_service

# Automation's conversation-scoped action vocabulary plus remove_tag.
ALLOWED_ACTIONS = frozenset(
    {
        "assign_user",
        "assign_team",
        "set_priority",
        "set_status",
        "add_tag",
        "remove_tag",
        "send_reply",
        "send_note",
    }
)

VISIBILITY_VALUES = frozenset(v.value for v in MacroVisibility)


def render_placeholders(
    content: str, *, contact_name: str | None = None, agent_name: str | None = None
) -> str:
    """Canned-response-style substitution ({{contact.name}} / {{agent.name}})."""
    return content.replace("{{contact.name}}", contact_name or "there").replace(
        "{{agent.name}}", agent_name or ""
    )


def _validate_visibility(visibility: str) -> None:
    if visibility not in VISIBILITY_VALUES:
        raise ValidationFailure(f"Unknown macro visibility: {visibility}")


def _validate_actions(actions: list[Any]) -> list[dict[str, Any]]:
    if not actions:
        raise ValidationFailure("A macro needs at least one action")
    normalized: list[dict[str, Any]] = []
    for raw in actions:
        item: dict[str, Any] = raw if isinstance(raw, dict) else raw.model_dump()
        action_type = item.get("type")
        if action_type not in ALLOWED_ACTIONS:
            raise ValidationFailure(f"Unknown macro action: {action_type}")
        params = item.get("params") or {}
        if not isinstance(params, dict):
            raise ValidationFailure("Macro action params must be an object")
        normalized.append({"type": action_type, "params": params})
    return normalized


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def get_macro(session: AsyncSession, workspace_id: str, macro_id: str) -> Macro:
    macro = await session.get(Macro, macro_id)
    if macro is None or macro.workspace_id != workspace_id:
        raise NotFoundError("Macro not found")
    return macro


async def list_macros(
    session: AsyncSession, workspace_id: str, *, user_id: str | None
) -> list[Macro]:
    """Global macros plus the caller's own personal ones."""
    visible = Macro.visibility == MacroVisibility.GLOBAL.value
    if user_id is not None:
        visible = or_(visible, Macro.created_by == user_id)
    result = await session.execute(
        select(Macro)
        .where(Macro.workspace_id == workspace_id, visible)
        .order_by(Macro.name, Macro.id)
    )
    return list(result.scalars())


async def create_macro(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    name: str,
    actions: list[Any],
    visibility: str = MacroVisibility.PERSONAL.value,
) -> Macro:
    _validate_visibility(visibility)
    macro = Macro(
        workspace_id=workspace_id,
        name=name.strip(),
        actions=_validate_actions(actions),
        visibility=visibility,
        created_by=actor.id if actor.type == "user" else None,
    )
    session.add(macro)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="macro.create",
        target_type="macro",
        target_id=macro.id,
        meta={"name": macro.name, "visibility": macro.visibility},
    )
    return macro


async def update_macro(
    session: AsyncSession,
    workspace_id: str,
    macro: Macro,
    *,
    actor: Actor,
    name: str | None = None,
    actions: list[Any] | None = None,
    visibility: str | None = None,
) -> Macro:
    assert macro.workspace_id == workspace_id
    if name is not None:
        macro.name = name.strip()
    if actions is not None:
        macro.actions = _validate_actions(actions)
    if visibility is not None:
        _validate_visibility(visibility)
        macro.visibility = visibility
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="macro.update",
        target_type="macro",
        target_id=macro.id,
        meta={"name": macro.name, "visibility": macro.visibility},
    )
    return macro


async def delete_macro(
    session: AsyncSession, workspace_id: str, macro: Macro, *, actor: Actor
) -> None:
    assert macro.workspace_id == workspace_id
    name = macro.name
    macro_id = macro.id
    await session.delete(macro)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="macro.delete",
        target_type="macro",
        target_id=macro_id,
        meta={"name": name},
    )


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


async def run_macro(
    session: AsyncSession,
    workspace_id: str,
    macro: Macro,
    conversation: Conversation,
    *,
    user: User,
    member_name: str,
) -> list[dict[str, Any]]:
    """Execute the macro's actions in order as the given member. Per-action
    errors are collected (ok=False) and execution continues."""
    actor = Actor(type="user", id=user.id, label=member_name)
    results: list[dict[str, Any]] = []
    for raw in macro.actions or []:
        item = raw if isinstance(raw, dict) else {}
        action_type = item.get("type")
        params: dict[str, Any] = item.get("params") or {}
        try:
            if action_type not in ALLOWED_ACTIONS:
                raise ValidationFailure(f"Unknown macro action: {action_type}")
            await _run_action(
                session,
                conversation,
                str(action_type),
                params,
                user=user,
                member_name=member_name,
                actor=actor,
            )
            results.append({"action": action_type, "ok": True, "error": None})
        except Exception as exc:  # noqa: BLE001 — collect and continue by contract
            results.append({"action": action_type, "ok": False, "error": str(exc)})
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="macro.run",
        target_type="macro",
        target_id=macro.id,
        meta={
            "name": macro.name,
            "conversation_id": conversation.id,
            "ok": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
        },
    )
    return results


async def _run_action(
    session: AsyncSession,
    conversation: Conversation,
    action_type: str,
    params: dict[str, Any],
    *,
    user: User,
    member_name: str,
    actor: Actor,
) -> None:
    workspace_id = conversation.workspace_id
    if action_type == "assign_user":
        user_id = params.get("user_id")
        if user_id == "self":  # Chatwoot's literal 'self' → the running member
            user_id = user.id
        await conversations_service.assign(
            session, conversation, assignee_user_id=user_id, actor=actor
        )
    elif action_type == "assign_team":
        await conversations_service.assign(
            session, conversation, team_id=params.get("team_id"), actor=actor
        )
    elif action_type == "set_priority":
        priority = params.get("priority")
        if not priority:
            raise ValidationFailure("set_priority requires a priority")
        await conversations_service.set_priority(session, conversation, str(priority), actor=actor)
    elif action_type == "set_status":
        status = params.get("status")
        if not status:
            raise ValidationFailure("set_status requires a status")
        await conversations_service.update_status(
            session,
            conversation,
            str(status),
            actor=actor,
            snoozed_until=_parse_dt(params.get("snoozed_until")),
        )
    elif action_type == "add_tag":
        tag_id = await _resolve_tag_id(session, workspace_id, params, create_missing=True)
        if tag_id is None:
            raise ValidationFailure("add_tag requires tag_id or tag")
        await conversations_service.add_tag(session, conversation, tag_id, actor=actor)
    elif action_type == "remove_tag":
        tag_id = await _resolve_tag_id(session, workspace_id, params, create_missing=False)
        if tag_id is None:
            raise ValidationFailure("remove_tag requires tag_id or tag")
        await conversations_service.remove_tag(session, conversation, tag_id, actor=actor)
    elif action_type in ("send_reply", "send_note"):
        content = params.get("content")
        if not content:
            raise ValidationFailure(f"{action_type} requires content")
        contact = await session.get(Contact, conversation.contact_id)
        rendered = render_placeholders(
            str(content),
            contact_name=contact.name if contact is not None else None,
            agent_name=member_name,
        )
        await conversations_service.add_message(
            session,
            conversation,
            direction=MessageDirection.OUT.value,
            author_type=AuthorType.USER.value,
            author_id=user.id,
            author_name=member_name,
            content=rendered,
            visibility=(
                MessageVisibility.PUBLIC.value
                if action_type == "send_reply"
                else MessageVisibility.NOTE.value
            ),
            actor=actor,
        )


async def _resolve_tag_id(
    session: AsyncSession, workspace_id: str, params: dict[str, Any], *, create_missing: bool
) -> str | None:
    """Mirror automation's tag resolution: tag_id wins, else look up by name
    (add_tag creates missing names; remove_tag never creates)."""
    tag_id = params.get("tag_id")
    if tag_id:
        return str(tag_id)
    name = params.get("tag")
    if not name:
        return None
    existing = (
        await session.execute(
            select(Tag).where(Tag.workspace_id == workspace_id, Tag.name == str(name).strip())
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    if not create_missing:
        raise NotFoundError("Tag not found")
    tag = Tag(workspace_id=workspace_id, name=str(name).strip())
    session.add(tag)
    await session.flush()
    return tag.id


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
