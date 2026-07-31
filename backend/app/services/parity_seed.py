"""Seed demo data for the competitive-parity wave: SLA policy, macros, campaign.

Idempotent — looks up by name before creating. The SLA policy is wired onto the
default widget inbox so new demo conversations get it auto-applied.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign
from app.models.inbox import ChannelType, Inbox
from app.models.macro import Macro
from app.models.sla import SlaPolicy
from app.seed import SeedContext


async def seed(session: AsyncSession, ctx: SeedContext) -> None:
    ws_id = ctx.workspace.id

    sla = (
        await session.execute(
            select(SlaPolicy).where(
                SlaPolicy.workspace_id == ws_id, SlaPolicy.name == "Standard support SLA"
            )
        )
    ).scalar_one_or_none()
    if sla is None:
        sla = SlaPolicy(
            workspace_id=ws_id,
            name="Standard support SLA",
            description="First reply in 15m, keep replying within 30m, resolve within 8h.",
            first_response_minutes=15,
            next_response_minutes=30,
            resolution_minutes=480,
        )
        session.add(sla)
        await session.flush()

    widget_inbox = (
        (
            await session.execute(
                select(Inbox)
                .where(Inbox.workspace_id == ws_id, Inbox.channel_type == ChannelType.WIDGET)
                .order_by(Inbox.created_at)
            )
        )
        .scalars()
        .first()
    )
    if widget_inbox is not None and widget_inbox.config.get("sla_policy_id") != sla.id:
        widget_inbox.config = {**widget_inbox.config, "sla_policy_id": sla.id}

    macros = {
        "Escalate to urgent": [
            {"type": "set_priority", "params": {"priority": "urgent"}},
            {"type": "add_tag", "params": {"tag": "escalated"}},
            {
                "type": "send_note",
                "params": {"content": "Escalated by {{agent.name}} — needs senior attention."},
            },
        ],
        "Close with thanks": [
            {
                "type": "send_reply",
                "params": {
                    "content": (
                        "Thanks for reaching out, {{contact.name}} — glad we could help! "
                        "This conversation is resolved; reply any time to reopen it."
                    )
                },
            },
            {"type": "set_status", "params": {"status": "resolved"}},
        ],
    }
    for name, actions in macros.items():
        existing = (
            await session.execute(
                select(Macro).where(Macro.workspace_id == ws_id, Macro.name == name)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Macro(
                    workspace_id=ws_id,
                    name=name,
                    actions=actions,
                    visibility="global",
                    created_by=ctx.owner.id,
                )
            )

    if widget_inbox is not None:
        campaign = (
            await session.execute(
                select(Campaign).where(
                    Campaign.workspace_id == ws_id, Campaign.title == "Docs page nudge"
                )
            )
        ).scalar_one_or_none()
        if campaign is None:
            session.add(
                Campaign(
                    workspace_id=ws_id,
                    title="Docs page nudge",
                    message=(
                        "Hi {{contact.name}} 👋 — stuck on anything in the docs? "
                        "Ask here and a human (or our AI agent) will help."
                    ),
                    campaign_type="ongoing",
                    status="active",
                    enabled=True,
                    inbox_id=widget_inbox.id,
                    sender_user_id=ctx.agent.id,
                    audience={"type": "all"},
                    trigger_rules={"url_pattern": "*/docs*", "time_on_page_seconds": 20},
                )
            )
    await session.flush()
