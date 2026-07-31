"""Action execution for automation rules.

Actions run through the same services humans/agents use, with a fixed system
actor so their side effects (activity entries, message author) are attributed to
"Automation". Actions needing a conversation are skipped when the triggering
event has none (e.g. contact.created).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.conditions import RuleContext
from app.core.events import Actor
from app.core.logging import log
from app.models.message import AuthorType, MessageDirection, MessageVisibility
from app.models.tag import Tag
from app.models.webhook import Webhook
from app.services import conversations as conversations_service
from app.services import notifications as notifications_service
from app.services import webhooks as webhooks_service

logger = log("automation")

AUTOMATION_ACTOR = Actor(type=AuthorType.SYSTEM.value, label="Automation")


async def _resolve_tag_id(
    session: AsyncSession, workspace_id: str, params: dict[str, Any]
) -> str | None:
    tag_id = params.get("tag_id")
    if tag_id:
        return str(tag_id)
    name = params.get("tag")
    if not name:
        return None
    existing = (
        await session.execute(select(Tag).where(Tag.workspace_id == workspace_id, Tag.name == name))
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    tag = Tag(workspace_id=workspace_id, name=str(name).strip())
    session.add(tag)
    await session.flush()
    return tag.id


async def run_action(
    session: AsyncSession, action_type: str, params: dict[str, Any], ctx: RuleContext
) -> None:
    workspace_id = ctx.event.workspace_id
    conversation = ctx.conversation

    if action_type == "notify_member":
        user_id = params.get("user_id")
        if user_id:
            await notifications_service.notify(
                session,
                workspace_id,
                str(user_id),
                type="automation",
                title=params.get("title") or "Automation",
                body=params.get("body"),
                link=params.get("link"),
            )
        return

    if action_type == "send_webhook":
        webhook_id = params.get("webhook_id")
        if not webhook_id:
            return
        webhook = await session.get(Webhook, str(webhook_id))
        if webhook is None or webhook.workspace_id != workspace_id or not webhook.enabled:
            return
        await webhooks_service.enqueue_delivery(
            session,
            webhook,
            workspace_id=workspace_id,
            event_name=ctx.event.name,
            event_payload=ctx.event.payload,
        )
        return

    # Everything below needs a conversation.
    if conversation is None:
        logger.debug("skipping action %s: no conversation in context", action_type)
        return

    if action_type == "assign_user":
        await conversations_service.assign(
            session, conversation, assignee_user_id=params.get("user_id"), actor=AUTOMATION_ACTOR
        )
    elif action_type == "assign_team":
        await conversations_service.assign(
            session, conversation, team_id=params.get("team_id"), actor=AUTOMATION_ACTOR
        )
    elif action_type == "set_priority":
        priority = params.get("priority")
        if priority:
            await conversations_service.set_priority(
                session, conversation, str(priority), actor=AUTOMATION_ACTOR
            )
    elif action_type == "set_status":
        status = params.get("status")
        if status:
            snoozed_until = _parse_dt(params.get("snoozed_until"))
            await conversations_service.update_status(
                session,
                conversation,
                str(status),
                actor=AUTOMATION_ACTOR,
                snoozed_until=snoozed_until,
            )
    elif action_type == "add_tag":
        tag_id = await _resolve_tag_id(session, workspace_id, params)
        if tag_id:
            await conversations_service.add_tag(
                session, conversation, tag_id, actor=AUTOMATION_ACTOR
            )
    elif action_type in ("send_reply", "send_note"):
        content = params.get("content")
        if content:
            visibility = (
                MessageVisibility.PUBLIC if action_type == "send_reply" else MessageVisibility.NOTE
            )
            await conversations_service.add_message(
                session,
                conversation,
                direction=MessageDirection.OUT,
                author_type=AuthorType.SYSTEM,
                author_id=None,
                author_name="Automation",
                content=str(content),
                visibility=visibility,
                actor=AUTOMATION_ACTOR,
            )


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
