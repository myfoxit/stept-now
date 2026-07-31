"""Service layer: answer validation matrix, append-only partial→completed
semantics, and the results math (NPS rounding, by_day, text answers)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.db import utcnow
from app.core.errors import ValidationFailure
from app.core.events import Actor
from app.models.survey import SurveyResponse
from app.services import surveys as service
from tests.surveys.conftest import (
    FULL_DECK,
    NPS_AND_TEXT,
    NPS_ID,
    RATING_ID,
    SELECT_ID,
    TEXT_ID,
    make_contact,
)


async def _survey(env, *, questions=None, name="Survey"):
    return await service.create_survey(
        env.session,
        env.workspace_id,
        actor=Actor(type="user", id=env.ctx.owner.id),
        name=name,
        questions=questions if questions is not None else FULL_DECK,
    )


async def test_answer_validation_matrix(survey_env):
    survey = await _survey(survey_env)
    bad_cases = [
        [{"question_id": "nope", "value": 5}],  # unknown question
        [{"question_id": NPS_ID, "value": 11}],  # nps out of range
        [{"question_id": NPS_ID, "value": -1}],
        [{"question_id": NPS_ID, "value": "eleven"}],  # not a number
        [{"question_id": RATING_ID, "value": 0}],  # rating out of range
        [{"question_id": RATING_ID, "value": 6}],
        [{"question_id": SELECT_ID, "value": "Telepathy"}],  # not an option
        [{"question_id": SELECT_ID, "value": 3}],  # wrong type
        [{"question_id": TEXT_ID, "value": 42}],  # wrong type
        [  # duplicate answer for the same question
            {"question_id": NPS_ID, "value": 9},
            {"question_id": NPS_ID, "value": 8},
        ],
    ]
    for answers in bad_cases:
        with pytest.raises(ValidationFailure):
            await service.submit_survey_response(
                survey_env.session,
                survey_env.workspace_id,
                survey,
                None,
                answers=answers,
                completed=False,
                meta={},
            )


async def test_required_questions_enforced_only_on_completion(survey_env):
    survey = await _survey(survey_env, questions=NPS_AND_TEXT)  # nps required, text optional

    # Partial submissions may skip anything.
    partial = await service.submit_survey_response(
        survey_env.session,
        survey_env.workspace_id,
        survey,
        None,
        answers=[],
        completed=False,
        meta={},
    )
    assert partial.completed is False

    with pytest.raises(ValidationFailure):
        await service.submit_survey_response(
            survey_env.session,
            survey_env.workspace_id,
            survey,
            None,
            answers=[{"question_id": TEXT_ID, "value": "no score though"}],
            completed=True,
            meta={},
        )

    done = await service.submit_survey_response(
        survey_env.session,
        survey_env.workspace_id,
        survey,
        None,
        answers=[{"question_id": NPS_ID, "value": 10}],
        completed=True,
        meta={"url": "https://app.example.com/inbox"},
    )
    assert done.completed is True
    assert done.meta == {"url": "https://app.example.com/inbox"}


async def test_blank_text_answers_are_dropped(survey_env):
    survey = await _survey(survey_env, questions=NPS_AND_TEXT)
    response = await service.submit_survey_response(
        survey_env.session,
        survey_env.workspace_id,
        survey,
        None,
        answers=[
            {"question_id": NPS_ID, "value": 8},
            {"question_id": TEXT_ID, "value": "   "},
        ],
        completed=True,
        meta={},
    )
    assert response.answers == [{"question_id": NPS_ID, "value": 8}]


async def test_partial_then_completed_appends_a_second_row(survey_env):
    survey = await _survey(survey_env, questions=NPS_AND_TEXT)
    contact = await make_contact(survey_env.session, survey_env.workspace_id)
    for completed in (False, True):
        await service.submit_survey_response(
            survey_env.session,
            survey_env.workspace_id,
            survey,
            contact.id,
            answers=[{"question_id": NPS_ID, "value": 9}],
            completed=completed,
            meta={},
        )
    total = (
        await survey_env.session.execute(
            select(func.count())
            .select_from(SurveyResponse)
            .where(SurveyResponse.survey_id == survey.id)
        )
    ).scalar_one()
    assert total == 2

    results = await service.compute_results(survey_env.session, survey_env.workspace_id, survey.id)
    assert results.responses == 2
    assert results.completed == 1
    assert results.completion_rate == 0.5
    # Only the completed row feeds the NPS math.
    assert results.nps is not None
    assert results.nps.promoters == 1


async def test_nps_math_rounds_and_ignores_partials(survey_env):
    survey = await _survey(survey_env, questions=NPS_AND_TEXT)
    # 3 promoters, 1 passive, 2 detractors of 6 completed → 50 − 33.33 = 16.67 → 17
    for score in (10, 9, 9, 7, 6, 0):
        await service.submit_survey_response(
            survey_env.session,
            survey_env.workspace_id,
            survey,
            None,
            answers=[{"question_id": NPS_ID, "value": score}],
            completed=True,
            meta={},
        )
    # A partial with an extreme score must not move the needle.
    await service.submit_survey_response(
        survey_env.session,
        survey_env.workspace_id,
        survey,
        None,
        answers=[{"question_id": NPS_ID, "value": 0}],
        completed=False,
        meta={},
    )

    results = await service.compute_results(survey_env.session, survey_env.workspace_id, survey.id)
    assert results.nps is not None
    assert (results.nps.promoters, results.nps.passives, results.nps.detractors) == (3, 1, 2)
    assert results.nps.score == 17
    assert results.responses == 7
    assert results.completed == 6


async def test_by_day_series_and_text_answers(survey_env):
    survey = await _survey(survey_env, questions=NPS_AND_TEXT)
    now = utcnow()
    for days_ago, text in ((40, "ancient"), (5, "recent"), (5, "also recent"), (0, "today")):
        survey_env.session.add(
            SurveyResponse(
                workspace_id=survey_env.workspace_id,
                survey_id=survey.id,
                answers=[
                    {"question_id": NPS_ID, "value": 9},
                    {"question_id": TEXT_ID, "value": text},
                ],
                completed=True,
                created_at=now - timedelta(days=days_ago),
            )
        )
    await survey_env.session.flush()

    results = await service.compute_results(survey_env.session, survey_env.workspace_id, survey.id)
    assert results.responses == 4  # totals include the out-of-window row
    dates = [point.date for point in results.by_day]
    assert dates == sorted(dates)
    assert len(dates) == 2  # 40 days ago falls outside the 30-day window
    assert {point.responses for point in results.by_day} == {1, 2}
    # Newest first, capped at 50.
    assert [answer.value for answer in results.text_answers][0] == "today"
    assert len(results.text_answers) == 4


async def test_results_empty_survey(survey_env):
    survey = await _survey(survey_env)
    results = await service.compute_results(survey_env.session, survey_env.workspace_id, survey.id)
    assert results.responses == 0
    assert results.completed == 0
    assert results.completion_rate == 0.0
    assert results.nps is None
    assert results.ratings is None
    assert results.by_day == []
    assert [s.question_id for s in results.select] == [SELECT_ID]
    assert results.select[0].counts == {"Search": 0, "A friend": 0, "Conference": 0}


async def test_results_are_workspace_scoped(survey_env):
    from app.core.errors import NotFoundError
    from app.models.workspace import Workspace

    survey = await _survey(survey_env)
    other = Workspace(name="Other", slug="other-results-ws")
    survey_env.session.add(other)
    await survey_env.session.flush()
    with pytest.raises(NotFoundError):
        await service.compute_results(survey_env.session, other.id, survey.id)
