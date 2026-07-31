"""DAP extra seed: shape, tour wiring (present + absent), and idempotency."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.checklist import Checklist, ChecklistProgress
from app.models.survey import Survey, SurveyResponse
from app.models.tour import Tour
from app.services import surveys as surveys_service
from app.services.dap_seed_extra import (
    CHECKLIST_NAME,
    SURVEY_NAME,
    WELCOME_TOUR_NAME,
    seed_checklists_surveys,
)
from tests.checklists.conftest import make_contact


async def _count(session, model, workspace_id) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
        )
    ).scalar_one()


async def test_seed_shape_and_idempotent(dap_env):
    session, ws = dap_env.session, dap_env.workspace_id
    for i in range(3):
        await make_contact(session, ws, name=f"Seeded {i}")
    session.add(Tour(workspace_id=ws, name=WELCOME_TOUR_NAME, steps=[], status="live"))
    await session.flush()

    await seed_checklists_surveys(session, dap_env.ctx)
    await seed_checklists_surveys(session, dap_env.ctx)  # must not duplicate anything

    assert await _count(session, Checklist, ws) == 1
    assert await _count(session, Survey, ws) == 1
    assert await _count(session, SurveyResponse, ws) == 6
    assert await _count(session, ChecklistProgress, ws) == 3

    checklist = (
        await session.execute(select(Checklist).where(Checklist.name == CHECKLIST_NAME))
    ).scalar_one()
    assert checklist.status == "live"
    assert [item["title"] for item in checklist.items] == [
        "Take the welcome tour",
        "Connect an inbox",
        "Invite a teammate",
    ]
    tour_id = (
        await session.execute(select(Tour.id).where(Tour.name == WELCOME_TOUR_NAME))
    ).scalar_one()
    assert checklist.items[0]["completion"] == {
        "type": "tour_completed",
        "tour_id": tour_id,
        "url_pattern": None,
    }
    assert checklist.items[0]["action"]["type"] == "start_tour"
    assert checklist.items[1]["completion"]["url_pattern"] == "*/settings*"

    survey = (await session.execute(select(Survey).where(Survey.name == SURVEY_NAME))).scalar_one()
    assert survey.status == "live"
    assert survey.presentation == "slideout"
    assert survey.trigger == {"type": "url_match", "url_pattern": "*/inbox*"}
    assert survey.frequency == {"type": "once"}
    assert [q["type"] for q in survey.questions] == ["nps", "text"]


async def test_seed_tolerates_missing_welcome_tour(dap_env):
    session, ws = dap_env.session, dap_env.workspace_id
    await seed_checklists_surveys(session, dap_env.ctx)

    checklist = (
        await session.execute(select(Checklist).where(Checklist.workspace_id == ws))
    ).scalar_one()
    assert checklist.items[0]["completion"]["type"] == "manual"
    assert checklist.items[0]["action"]["type"] == "none"
    # No contacts in this workspace → no demo progress rows, responses stay anonymous.
    assert await _count(session, ChecklistProgress, ws) == 0


async def test_seeded_survey_results_render(dap_env):
    session, ws = dap_env.session, dap_env.workspace_id
    await seed_checklists_surveys(session, dap_env.ctx)
    survey = (await session.execute(select(Survey).where(Survey.workspace_id == ws))).scalar_one()

    results = await surveys_service.compute_results(session, ws, survey.id)
    assert results.responses == 6
    assert results.completed == 5
    assert results.nps is not None
    # completed scores: 10, 9, 8, 7, 6 → 2 promoters, 2 passives, 1 detractor.
    assert (results.nps.promoters, results.nps.passives, results.nps.detractors) == (2, 2, 1)
    assert results.nps.score == 20
    assert len(results.text_answers) == 4
    assert len(results.by_day) == 6  # one bucket per distinct seeded day within 30d
    assert results.by_day == sorted(results.by_day, key=lambda p: p.date)
