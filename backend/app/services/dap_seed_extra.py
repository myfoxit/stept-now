"""Demo seed for the extra DAP experiences: one live checklist + one live survey.

Called by `app/dap/seed.py` (agent A1 owns that file) as
`seed_checklists_surveys(session, ctx)`. Idempotent by name: re-runs add nothing.

The first checklist item auto-completes when the seeded "Welcome to Stept" tour
is completed — looked up by name, tolerating its absence (the item degrades to a
manual checkbox so the seed never depends on tour seeding order).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.events import Actor
from app.models.checklist import Checklist, ChecklistProgress
from app.models.contact import Contact
from app.models.survey import Survey, SurveyResponse
from app.models.tour import Tour
from app.seed import SeedContext
from app.services import checklists as checklists_service
from app.services import surveys as surveys_service

CHECKLIST_NAME = "Getting started with Stept"
SURVEY_NAME = "How are we doing?"
WELCOME_TOUR_NAME = "Welcome to Stept"

NPS_QUESTION_ID = "q-nps"
TEXT_QUESTION_ID = "q-reason"

SURVEY_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": NPS_QUESTION_ID,
        "type": "nps",
        "question": "How likely are you to recommend Stept to a colleague?",
        "required": True,
    },
    {
        "id": TEXT_QUESTION_ID,
        "type": "text",
        "question": "What is the main reason for your score?",
        "required": False,
    },
]

# (nps, text, completed, days_ago) — 5 completed + 1 partial.
SEED_RESPONSES: list[tuple[int, str, bool, int]] = [
    (10, "The shared inbox finally replaced our email chaos.", True, 12),
    (9, "AI drafts with citations save us hours every week.", True, 9),
    (8, "", True, 6),
    (7, "Search across articles could be a bit faster.", True, 4),
    (6, "Reporting needs more breakdowns before we roll it out.", True, 2),
    (9, "", False, 1),
]


async def _checklist_items(session: AsyncSession, workspace_id: str) -> list[dict[str, Any]]:
    welcome = (
        await session.execute(
            select(Tour).where(Tour.workspace_id == workspace_id, Tour.name == WELCOME_TOUR_NAME)
        )
    ).scalar_one_or_none()
    tour_action: dict[str, Any] = {"type": "none"}
    tour_completion: dict[str, Any] = {"type": "manual"}
    if welcome is not None:
        tour_action = {"type": "start_tour", "tour_id": welcome.id}
        tour_completion = {"type": "tour_completed", "tour_id": welcome.id}
    return [
        {
            "id": "take-the-welcome-tour",
            "title": "Take the welcome tour",
            "body": "A two-minute walk through the inbox, knowledge base, and AI agent.",
            "action": tour_action,
            "completion": tour_completion,
        },
        {
            "id": "connect-an-inbox",
            "title": "Connect an inbox",
            "body": "Bring email, chat, or Slack conversations into Stept.",
            "action": {"type": "open_url", "url": "/settings/channels"},
            "completion": {"type": "url_visited", "url_pattern": "*/settings*"},
        },
        {
            "id": "invite-a-teammate",
            "title": "Invite a teammate",
            "body": "Support works better together — invite the rest of your team.",
            "action": {"type": "open_url", "url": "/settings/members"},
            "completion": {"type": "manual"},
        },
    ]


async def _demo_contact_ids(session: AsyncSession, workspace_id: str, limit: int) -> list[str]:
    rows = (
        await session.execute(
            select(Contact.id)
            .where(Contact.workspace_id == workspace_id)
            .order_by(Contact.created_at.asc())
            .limit(limit)
        )
    ).scalars()
    return list(rows)


async def _seed_checklist(session: AsyncSession, ctx: SeedContext, actor: Actor) -> None:
    workspace_id = ctx.workspace.id
    existing = (
        await session.execute(
            select(Checklist).where(
                Checklist.workspace_id == workspace_id, Checklist.name == CHECKLIST_NAME
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return

    checklist = await checklists_service.create_checklist(
        session,
        workspace_id,
        actor=actor,
        name=CHECKLIST_NAME,
        description="Three steps to a working support workspace.",
        items=await _checklist_items(session, workspace_id),
        trigger={"type": "url_match", "url_pattern": "*"},
        audience={"type": "all"},
        theme={"accent": "#6366f1", "position": "bottom-right"},
        launcher={"label": "Getting started", "auto_open_once": True},
        priority=10,
    )
    await checklists_service.publish_checklist(session, workspace_id, checklist.id, actor=actor)

    # A little progress so the stats strip is not empty out of the box.
    item_ids = [item["id"] for item in checklist.items]
    contact_ids = await _demo_contact_ids(session, workspace_id, 3)
    now = utcnow()
    for index, contact_id in enumerate(contact_ids):
        done = item_ids[: index + 1]
        session.add(
            ChecklistProgress(
                workspace_id=workspace_id,
                checklist_id=checklist.id,
                contact_id=contact_id,
                item_state={
                    item_id: (now - timedelta(days=index + 1)).isoformat() for item_id in done
                },
                completed_at=now - timedelta(days=1) if len(done) == len(item_ids) else None,
            )
        )
    await session.flush()


async def _seed_survey(session: AsyncSession, ctx: SeedContext, actor: Actor) -> None:
    workspace_id = ctx.workspace.id
    existing = (
        await session.execute(
            select(Survey).where(Survey.workspace_id == workspace_id, Survey.name == SURVEY_NAME)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return

    survey = await surveys_service.create_survey(
        session,
        workspace_id,
        actor=actor,
        name=SURVEY_NAME,
        questions=SURVEY_QUESTIONS,
        presentation="slideout",
        trigger={"type": "url_match", "url_pattern": "*/inbox*"},
        audience={"type": "all"},
        schedule={},
        frequency={"type": "once"},
        priority=0,
        theme={"accent": "#6366f1"},
        thanks_message="Thanks for the feedback! It goes straight to the product team.",
    )
    await surveys_service.publish_survey(session, workspace_id, survey.id, actor=actor)

    contact_ids = await _demo_contact_ids(session, workspace_id, len(SEED_RESPONSES))
    now = utcnow()
    for index, (score, text, completed, days_ago) in enumerate(SEED_RESPONSES):
        answers: list[dict[str, Any]] = [{"question_id": NPS_QUESTION_ID, "value": score}]
        if text:
            answers.append({"question_id": TEXT_QUESTION_ID, "value": text})
        session.add(
            SurveyResponse(
                workspace_id=workspace_id,
                survey_id=survey.id,
                contact_id=contact_ids[index] if index < len(contact_ids) else None,
                answers=answers,
                completed=completed,
                meta={"url": "https://app.stept.dev/inbox"},
                created_at=now - timedelta(days=days_ago),
            )
        )
    await session.flush()


async def seed_checklists_surveys(session: AsyncSession, ctx: SeedContext) -> None:
    """Seed the demo checklist + survey (idempotent by name)."""
    actor = Actor(type="user", id=ctx.owner.id, label=ctx.owner.name)
    await _seed_checklist(session, ctx, actor)
    await _seed_survey(session, ctx, actor)
