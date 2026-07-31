"""Demo DAP seed: a live onboarding tour + a draft, plus playback telemetry.

The live "Welcome to Stept" tour anchors to the dashboard sidebar
(`[data-tour="inbox"|"knowledge"|"ai"]`) and triggers on the inbox URL. A handful
of TourEvents describe a realistic funnel (3 starts → 1 completion) so the stats
endpoint shows real numbers out of the box. Idempotent: re-runs add nothing.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import uuid7
from app.core.events import Actor
from app.models.tour import Tour, TourEvent
from app.seed import SeedContext
from app.services import tours as tours_service

WELCOME_NAME = "Welcome to Stept"
DRAFT_NAME = "Discover automations"

WELCOME_STEPS = [
    {
        "selector": '[data-tour="inbox"]',
        "title": "Your shared inbox",
        "body": "Every conversation from every channel lands here for your whole team.",
        "placement": "right",
    },
    {
        "selector": '[data-tour="knowledge"]',
        "title": "Build your knowledge base",
        "body": "Add docs and help-center articles so answers stay consistent.",
        "placement": "right",
    },
    {
        "selector": '[data-tour="ai"]',
        "title": "Let AI help",
        "body": "Connect a model and your AI agent drafts replies with citations.",
        "placement": "right",
    },
]

DRAFT_STEPS = [
    {
        "selector": '[data-tour="automations"]',
        "title": "Automate the busywork",
        "body": "Route, tag, and reply automatically with rules.",
        "placement": "right",
    },
]


async def _seed_welcome_events(session: AsyncSession, workspace_id: str, tour_id: str) -> None:
    """A small realistic funnel: 3 starts, 1 completion, 2 dismissals."""
    c1, c2, c3 = uuid7(), uuid7(), uuid7()
    plays: list[tuple[str, str, int | None]] = [
        # c1 completes the whole tour
        (c1, "started", None),
        (c1, "step_viewed", 0),
        (c1, "step_viewed", 1),
        (c1, "step_viewed", 2),
        (c1, "completed", None),
        # c2 drops off after the second step
        (c2, "started", None),
        (c2, "step_viewed", 0),
        (c2, "step_viewed", 1),
        (c2, "dismissed", None),
        # c3 drops off after the first step
        (c3, "started", None),
        (c3, "step_viewed", 0),
        (c3, "dismissed", None),
    ]
    for contact_id, event, step_index in plays:
        session.add(
            TourEvent(
                workspace_id=workspace_id,
                tour_id=tour_id,
                contact_id=contact_id,
                event=event,
                step_index=step_index,
            )
        )
    await session.flush()


async def _get_by_name(session: AsyncSession, workspace_id: str, name: str) -> Tour | None:
    return (
        await session.execute(
            select(Tour).where(Tour.workspace_id == workspace_id, Tour.name == name)
        )
    ).scalar_one_or_none()


async def seed(session: AsyncSession, ctx: SeedContext) -> None:
    workspace_id = ctx.workspace.id
    actor = Actor(type="user", id=ctx.owner.id, label=ctx.owner.name)

    if await _get_by_name(session, workspace_id, WELCOME_NAME) is None:
        tour = await tours_service.create_tour(
            session,
            workspace_id,
            actor=actor,
            name=WELCOME_NAME,
            description="A three-step introduction to the Stept dashboard.",
            trigger={"type": "url_match", "url_pattern": "*/inbox*"},
            audience={"type": "all"},
            steps=WELCOME_STEPS,
            theme={"accent": "#6366f1"},
        )
        await tours_service.publish_tour(session, workspace_id, tour.id, actor=actor)
        # Seed telemetry only when the tour is first created (keeps re-runs clean).
        already = (
            await session.execute(
                select(func.count()).select_from(TourEvent).where(TourEvent.tour_id == tour.id)
            )
        ).scalar_one()
        if not already:
            await _seed_welcome_events(session, workspace_id, tour.id)

    if await _get_by_name(session, workspace_id, DRAFT_NAME) is None:
        await tours_service.create_tour(
            session,
            workspace_id,
            actor=actor,
            name=DRAFT_NAME,
            description="Work in progress — not yet published.",
            trigger={"type": "manual"},
            audience={"type": "all"},
            steps=DRAFT_STEPS,
            theme={"accent": "#6366f1"},
        )
