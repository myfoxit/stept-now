"""Directory demo seed: contacts, tags, team, canned responses, segment.

Idempotent — safe to run `python -m app.seed` repeatedly.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.canned_response import CannedResponse
from app.models.contact import Contact
from app.models.segment import Segment
from app.models.tag import ContactTag, Tag
from app.models.team import Team, TeamMember

if TYPE_CHECKING:
    from app.seed import SeedContext

# (name, email, external_id, verified, days_since_seen, attributes)
_CONTACTS: list[tuple[str, str, str | None, bool, int | None, dict[str, Any]]] = [
    (
        "Ada Lovelace",
        "ada@analytical.io",
        "cust_1001",
        True,
        0,
        {"plan": "enterprise", "company": "Analytical Engines"},
    ),
    (
        "Grace Hopper",
        "grace@navy.example",
        "cust_1002",
        True,
        1,
        {"plan": "enterprise", "company": "US Navy Labs"},
    ),
    (
        "Linus T",
        "linus@kernel.example",
        "cust_1003",
        True,
        2,
        {"plan": "pro", "company": "Kernel Works"},
    ),
    (
        "Margaret Hamilton",
        "margaret@apollo.example",
        "cust_1004",
        True,
        3,
        {"plan": "pro", "company": "Apollo Software"},
    ),
    ("Ken Thompson", "ken@bell.example", None, False, 5, {"plan": "free", "company": "Bell Labs"}),
    (
        "Radia Perlman",
        "radia@spanning.example",
        "cust_1006",
        True,
        8,
        {"plan": "pro", "company": "Spanning Tree Inc"},
    ),
    ("Barbara Liskov", "barbara@substitution.example", None, False, 13, {"plan": "free"}),
    (
        "Anonymous Visitor",
        "visitor-77@example.com",
        None,
        False,
        None,
        {"plan": "free", "source": "widget"},
    ),
    (
        "Donald Knuth",
        "don@taocp.example",
        "cust_1009",
        True,
        21,
        {"plan": "enterprise", "company": "TAOCP Press"},
    ),
]

_TAGS = [("vip", "#f59e0b"), ("bug", "#ef4444"), ("billing", "#3b82f6")]

_CANNED = [
    (
        "greeting",
        "Hi {{contact.name}}, thanks for reaching out! I'm {{agent.name}} — "
        "how can I help you today?",
    ),
    (
        "refund-policy",
        "We offer full refunds within **30 days** of purchase. "
        "Share your order number and I'll take care of it right away.",
    ),
    (
        "bug-received",
        "Thanks for the report, {{contact.name}}! Our engineers are on it — "
        "I'll follow up here as soon as we have a fix.",
    ),
    (
        "closing",
        "Glad we could sort that out! If anything else comes up, just reply here. "
        "Have a great day! — {{agent.name}}",
    ),
]


async def seed(session: AsyncSession, ctx: SeedContext) -> None:
    workspace_id = ctx.workspace.id
    now = utcnow()

    # --- contacts (check by email) ---
    for name, email, external_id, verified, days_ago, attributes in _CONTACTS:
        existing = (
            await session.execute(
                select(Contact.id).where(
                    Contact.workspace_id == workspace_id, Contact.email == email
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        seen = now - timedelta(days=days_ago) if days_ago is not None else None
        session.add(
            Contact(
                workspace_id=workspace_id,
                name=name,
                email=email,
                external_id=external_id,
                verified=verified,
                attributes=attributes,
                first_seen_at=seen - timedelta(days=30) if seen else None,
                last_seen_at=seen,
            )
        )
    await session.flush()

    # --- tags (check by name) ---
    tags_by_name: dict[str, Tag] = {}
    for tag_name, color in _TAGS:
        tag = (
            await session.execute(
                select(Tag).where(Tag.workspace_id == workspace_id, Tag.name == tag_name)
            )
        ).scalar_one_or_none()
        if tag is None:
            tag = Tag(workspace_id=workspace_id, name=tag_name, color=color)
            session.add(tag)
            await session.flush()
        tags_by_name[tag_name] = tag

    # vip tag on enterprise contacts
    vip = tags_by_name["vip"]
    enterprise_ids = (
        (
            await session.execute(
                select(Contact.id).where(
                    Contact.workspace_id == workspace_id,
                    Contact.email.in_(["ada@analytical.io", "don@taocp.example"]),
                )
            )
        )
        .scalars()
        .all()
    )
    for contact_id in enterprise_ids:
        linked = (
            await session.execute(
                select(ContactTag.id).where(
                    ContactTag.contact_id == contact_id, ContactTag.tag_id == vip.id
                )
            )
        ).scalar_one_or_none()
        if linked is None:
            session.add(ContactTag(workspace_id=workspace_id, contact_id=contact_id, tag_id=vip.id))

    # --- team "Support" with both demo users ---
    team = (
        await session.execute(
            select(Team).where(Team.workspace_id == workspace_id, Team.name == "Support")
        )
    ).scalar_one_or_none()
    if team is None:
        team = Team(
            workspace_id=workspace_id,
            name="Support",
            icon="🎧",
            description="Frontline customer support",
        )
        session.add(team)
        await session.flush()
    for user in (ctx.owner, ctx.agent):
        member = (
            await session.execute(
                select(TeamMember.id).where(
                    TeamMember.team_id == team.id, TeamMember.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if member is None:
            session.add(TeamMember(workspace_id=workspace_id, team_id=team.id, user_id=user.id))

    # --- canned responses (check by shortcut) ---
    for shortcut, content in _CANNED:
        existing_canned = (
            await session.execute(
                select(CannedResponse.id).where(
                    CannedResponse.workspace_id == workspace_id,
                    CannedResponse.shortcut == shortcut,
                )
            )
        ).scalar_one_or_none()
        if existing_canned is None:
            session.add(
                CannedResponse(
                    workspace_id=workspace_id,
                    shortcut=shortcut,
                    content=content,
                    created_by=ctx.owner.id,
                )
            )

    # --- segment "Enterprise customers" (check by name) ---
    segment = (
        await session.execute(
            select(Segment.id).where(
                Segment.workspace_id == workspace_id, Segment.name == "Enterprise customers"
            )
        )
    ).scalar_one_or_none()
    if segment is None:
        session.add(
            Segment(
                workspace_id=workspace_id,
                name="Enterprise customers",
                filters=[{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
                created_by=ctx.owner.id,
            )
        )

    await session.flush()
