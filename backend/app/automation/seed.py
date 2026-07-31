"""Demo automation + webhook seed (idempotent). Called by app.seed._seed_domains."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventNames
from app.core.security import new_token
from app.models.automation import AutomationRule
from app.models.webhook import Webhook


async def _ensure_rule(session: AsyncSession, workspace_id: str, name: str, **fields: Any) -> None:
    existing = (
        await session.execute(
            select(AutomationRule).where(
                AutomationRule.workspace_id == workspace_id, AutomationRule.name == name
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(AutomationRule(workspace_id=workspace_id, name=name, **fields))
    await session.flush()


async def seed(session: AsyncSession, ctx: Any) -> None:
    workspace_id = ctx.workspace.id
    created_by = ctx.owner.id

    await _ensure_rule(
        session,
        workspace_id,
        "VIP tagging",
        event=EventNames.CONVERSATION_CREATED,
        conditions=[{"field": "contact.attributes.plan", "op": "eq", "value": "enterprise"}],
        actions=[
            {"type": "add_tag", "params": {"tag": "vip"}},
            {"type": "set_priority", "params": {"priority": "high"}},
        ],
        enabled=True,
        ord=0,
        created_by=created_by,
    )

    await _ensure_rule(
        session,
        workspace_id,
        "Away autoreply",
        event=EventNames.MESSAGE_CREATED,
        conditions=[],
        actions=[
            {
                "type": "send_reply",
                "params": {
                    "content": "Thanks for reaching out! Our team is away and will reply soon."
                },
            }
        ],
        enabled=False,
        ord=1,
        created_by=created_by,
    )

    existing_webhook = (
        await session.execute(
            select(Webhook).where(
                Webhook.workspace_id == workspace_id,
                Webhook.url == "https://example.com/stept-webhook",
            )
        )
    ).scalar_one_or_none()
    if existing_webhook is None:
        session.add(
            Webhook(
                workspace_id=workspace_id,
                url="https://example.com/stept-webhook",
                secret=new_token(24),
                events=[
                    EventNames.CONVERSATION_CREATED,
                    EventNames.CONVERSATION_STATUS_CHANGED,
                ],
                enabled=False,
                description="Example outbound webhook (disabled)",
            )
        )
        await session.flush()
