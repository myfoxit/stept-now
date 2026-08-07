"""Bulk conversation actions.

A bulk request names its targets either explicitly (`conversation_ids`) or by
filter document (`query`, same DSL as saved views), then applies one action to
each. Every mutation goes through the normal `conversations` service functions —
bulk is a loop, never a shortcut around trackers, events, activity notes or
realtime broadcasts, because a silent bulk path is how audit trails rot.

Per-conversation failures are collected instead of aborting the batch, matching
how Chatwoot's macro executor handles a bad action mid-run.

See docs/CHATWOOT-BACKLOG.md §1.4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationFailure
from app.core.events import Actor
from app.models.conversation import Conversation, ConversationPriority, ConversationStatus
from app.services import audit, filters
from app.services import conversations as conversations_service

# Hard ceiling per request: keeps one call bounded, and anything larger wants a
# background job rather than a synchronous HTTP request.
MAX_TARGETS = 500

ACTIONS = (
    "set_status",
    "set_priority",
    "assign_user",
    "assign_team",
    "add_tag",
    "remove_tag",
)


@dataclass
class BulkResult:
    requested: int = 0
    succeeded: int = 0
    failed: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)


async def _resolve_targets(
    session: AsyncSession,
    workspace_id: str,
    *,
    conversation_ids: list[str] | None,
    query: dict[str, Any] | None,
) -> list[Conversation]:
    if conversation_ids:
        if len(conversation_ids) > MAX_TARGETS:
            raise ValidationFailure(f"At most {MAX_TARGETS} conversations per bulk action")
        rows = list(
            (
                await session.execute(
                    select(Conversation).where(
                        Conversation.workspace_id == workspace_id,
                        Conversation.id.in_(conversation_ids),
                    )
                )
            ).scalars()
        )
        # Preserve the caller's order so the result reads predictably.
        by_id = {c.id: c for c in rows}
        return [by_id[cid] for cid in conversation_ids if cid in by_id]

    if not query:
        raise ValidationFailure("Provide either conversation_ids or a query")
    condition, post_filters, _ = filters.compile_conversation_filter(query)
    statement = select(Conversation).where(Conversation.workspace_id == workspace_id)
    if condition is not None:
        statement = statement.where(condition)
    statement = statement.order_by(
        Conversation.last_activity_at.desc(), Conversation.id.desc()
    ).limit(MAX_TARGETS + 1)
    rows = [
        c
        for c in (await session.execute(statement)).scalars()
        if filters.passes_post_filters(c, post_filters)
    ]
    if len(rows) > MAX_TARGETS:
        raise ValidationFailure(
            f"Filter matches more than {MAX_TARGETS} conversations — narrow it first"
        )
    return rows


def _parse_snooze(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationFailure("snoozed_until must be an ISO datetime") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _validate_action(action: str, params: dict[str, Any]) -> None:
    if action not in ACTIONS:
        raise ValidationFailure(f"Unknown bulk action {action!r}")
    if action == "set_status":
        value = params.get("status")
        if value not in ConversationStatus:
            raise ValidationFailure(f"Unknown status: {value}")
        if value == ConversationStatus.SNOOZED.value and not params.get("snoozed_until"):
            raise ValidationFailure("Snoozing in bulk requires snoozed_until")
    elif action == "set_priority":
        if params.get("priority") not in ConversationPriority:
            raise ValidationFailure(f"Unknown priority: {params.get('priority')}")
    elif action in ("add_tag", "remove_tag") and not params.get("tag_id"):
        raise ValidationFailure(f"{action} requires tag_id")
    elif action == "assign_team" and params.get("team_id") is None and "team_id" not in params:
        raise ValidationFailure("assign_team requires team_id (null to unassign)")


async def _apply_one(
    session: AsyncSession,
    workspace_id: str,
    conversation: Conversation,
    action: str,
    params: dict[str, Any],
    *,
    actor: Actor,
    actor_user_id: str | None,
) -> None:
    if action == "set_status":
        await conversations_service.update_status(
            session,
            conversation,
            str(params["status"]),
            snoozed_until=_parse_snooze(params.get("snoozed_until")),
            actor=actor,
        )
    elif action == "set_priority":
        await conversations_service.set_priority(
            session, conversation, str(params["priority"]), actor=actor
        )
    elif action == "assign_user":
        target = params.get("assignee_user_id")
        if target == "self":
            if actor_user_id is None:
                raise ValidationFailure("assign_user='self' requires a user principal")
            target = actor_user_id
        await conversations_service.assign(
            session, conversation, assignee_user_id=target, actor=actor
        )
    elif action == "assign_team":
        await conversations_service.assign(
            session, conversation, team_id=params.get("team_id"), actor=actor
        )
    elif action == "add_tag":
        await conversations_service.add_tag(
            session, conversation, str(params["tag_id"]), actor=actor
        )
    elif action == "remove_tag":
        await conversations_service.remove_tag(
            session, conversation, str(params["tag_id"]), actor=actor
        )


async def run(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    actor_user_id: str | None,
    action: str,
    params: dict[str, Any] | None = None,
    conversation_ids: list[str] | None = None,
    query: dict[str, Any] | None = None,
) -> BulkResult:
    params = params or {}
    _validate_action(action, params)
    targets = await _resolve_targets(
        session, workspace_id, conversation_ids=conversation_ids, query=query
    )
    result = BulkResult(requested=len(targets))
    for conversation in targets:
        try:
            await _apply_one(
                session,
                workspace_id,
                conversation,
                action,
                params,
                actor=actor,
                actor_user_id=actor_user_id,
            )
            result.succeeded += 1
        except (ValidationFailure, ForbiddenError, NotFoundError) as exc:
            result.failed += 1
            result.errors.append({"conversation_id": conversation.id, "error": str(exc)})
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="conversations.bulk",
        target_type="conversation",
        target_id=None,
        meta={
            "bulk_action": action,
            "requested": result.requested,
            "succeeded": result.succeeded,
            "failed": result.failed,
        },
    )
    return result
