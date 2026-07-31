"""Demo DAP seed: live onboarding tour, a banner, a driven draft, plus telemetry.

The live "Welcome to Stept" tour anchors to the dashboard sidebar
(`[data-tour="inbox"|"knowledge"|"ai"]`) and triggers on the inbox URL. Its
TourEvents describe a realistic funnel (4 starts → 1 completion, one self-healed
step view and one step error) so every analytics tile shows real numbers out of
the box. Idempotent: re-runs add nothing.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import uuid7
from app.core.events import Actor
from app.models.tour import Tour, TourEvent
from app.seed import SeedContext
from app.services import tours as tours_service

WELCOME_NAME = "Welcome to Stept"
DRAFT_NAME = "Discover automations"
BANNER_NAME = "What's new in Stept"
DRIVEN_NAME = "Create your first automation"

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

BANNER_STEPS: list[dict[str, Any]] = [
    {
        "type": "banner",
        "title": "New: AI answers with citations",
        "body": "Your AI agent now cites the article it answered from. **Try it in the inbox.**",
        "placement": "center",
    }
]

# Do-it-for-me flow: click into automations, wait for the editor, then explain it.
DRIVEN_STEPS: list[dict[str, Any]] = [
    {
        "type": "action",
        "selector": '[data-tour="automations"]',
        "text_hint": "Automations",
        "title": "Opening automations",
        "action": {"kind": "click"},
        "advance": {"on": "element_click"},
    },
    {
        "type": "wait",
        "selector": '[data-tour="new-rule"]',
        "title": "Loading",
        "wait": {"for": "element", "selector": '[data-tour="new-rule"]', "timeout_ms": 8000},
    },
    {
        "type": "tooltip",
        "selector": '[data-tour="new-rule"]',
        "text_hint": "New rule",
        "title": "Create your first rule",
        "body": "Rules run on every new conversation — route, tag, or auto-reply.",
        "placement": "bottom",
    },
]


async def _seed_welcome_events(session: AsyncSession, workspace_id: str, tour_id: str) -> None:
    """4 starts, 1 completion, 3 dismissals, 1 healed view, 1 step error."""
    c1, c2, c3, c4 = uuid7(), uuid7(), uuid7(), uuid7()
    plays: list[tuple[str, str, int | None, dict[str, Any]]] = [
        # c1 completes the whole tour
        (c1, "started", None, {}),
        (c1, "step_viewed", 0, {}),
        (c1, "step_viewed", 1, {}),
        (c1, "step_viewed", 2, {}),
        (c1, "completed", None, {}),
        # c2 drops off after the second step
        (c2, "started", None, {}),
        (c2, "step_viewed", 0, {}),
        (c2, "step_viewed", 1, {}),
        (c2, "dismissed", None, {}),
        # c3 drops off after the first step
        (c3, "started", None, {}),
        (c3, "step_viewed", 0, {}),
        (c3, "dismissed", None, {}),
        # c4 hits a moved selector: step 0 self-heals, step 1 cannot be found
        (c4, "started", None, {}),
        (c4, "step_viewed", 0, {"url": "https://app.example.com/inbox", "healed": True}),
        (c4, "step_error", 1, {"reason": "not_found"}),
        (c4, "dismissed", None, {}),
    ]
    for contact_id, event, step_index, meta in plays:
        session.add(
            TourEvent(
                workspace_id=workspace_id,
                tour_id=tour_id,
                contact_id=contact_id,
                event=event,
                step_index=step_index,
                meta=meta,
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

    if await _get_by_name(session, workspace_id, BANNER_NAME) is None:
        banner = await tours_service.create_tour(
            session,
            workspace_id,
            actor=actor,
            name=BANNER_NAME,
            description="Product announcement bar, shown once per visitor.",
            kind="banner",
            trigger={"type": "url_match", "url_pattern": "*"},
            audience={"type": "all"},
            frequency={"type": "once"},
            priority=10,
            settings={"backdrop": False, "show_progress": False},
            steps=BANNER_STEPS,
            theme={"accent": "#0ea5e9", "position": "top"},
        )
        await tours_service.publish_tour(session, workspace_id, banner.id, actor=actor)

    if await _get_by_name(session, workspace_id, DRIVEN_NAME) is None:
        await tours_service.create_tour(
            session,
            workspace_id,
            actor=actor,
            name=DRIVEN_NAME,
            description="Do-it-for-me flow: Stept drives the UI for the user.",
            trigger={"type": "manual"},
            audience={"type": "all"},
            settings={"mode": "driven", "show_progress": True},
            steps=DRIVEN_STEPS,
            theme={"accent": "#6366f1"},
        )

    # Checklists/surveys ship in a sibling module; seed them when present.
    try:
        from app.services.dap_seed_extra import seed_checklists_surveys
    except ImportError:
        return
    await seed_checklists_surveys(session, ctx)
