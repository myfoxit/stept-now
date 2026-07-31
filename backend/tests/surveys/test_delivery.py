"""Service layer: widget delivery — URL trigger, schedule window, frequency
matrix (incl. cooldown + anonymous), audience, and priority ordering."""

from __future__ import annotations

from datetime import timedelta

from app.core.db import utcnow
from app.core.events import Actor
from app.models.survey import SurveyResponse
from app.services import surveys as service
from tests.surveys.conftest import NPS_AND_TEXT, NPS_ID, make_contact

INBOX_URL = "https://app.example.com/inbox?tab=open"


async def _live_survey(env, *, name="Survey", questions=None, **overrides):
    actor = Actor(type="user", id=env.ctx.owner.id, label=env.ctx.owner.name)
    survey = await service.create_survey(
        env.session,
        env.workspace_id,
        actor=actor,
        name=name,
        questions=questions if questions is not None else NPS_AND_TEXT,
        **overrides,
    )
    return await service.publish_survey(env.session, env.workspace_id, survey.id, actor=actor)


async def _respond(env, survey, contact, *, completed: bool, days_ago: int = 0) -> None:
    env.session.add(
        SurveyResponse(
            workspace_id=env.workspace_id,
            survey_id=survey.id,
            contact_id=contact.id if contact is not None else None,
            answers=[{"question_id": NPS_ID, "value": 9}],
            completed=completed,
            created_at=utcnow() - timedelta(days=days_ago),
        )
    )
    await env.session.flush()


async def test_delivery_public_shape_and_url_match(survey_env):
    survey = await _live_survey(
        survey_env, trigger={"type": "url_match", "url_pattern": "*/inbox*"}
    )
    delivered = await service.deliverable_surveys(
        survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=None
    )
    assert [s["id"] for s in delivered] == [survey.id]
    assert set(delivered[0]) == {
        "id",
        "name",
        "questions",
        "presentation",
        "theme",
        "thanks_message",
        "version",
        "frequency_type",
    }
    assert delivered[0]["frequency_type"] == "once"

    elsewhere = await service.deliverable_surveys(
        survey_env.session,
        survey_env.workspace_id,
        url="https://app.example.com/settings",
        contact=None,
    )
    assert elsewhere == []


async def test_frequency_once_excludes_after_any_response(survey_env):
    survey = await _live_survey(survey_env, frequency={"type": "once"})
    contact = await make_contact(survey_env.session, survey_env.workspace_id)

    assert (
        len(
            await service.deliverable_surveys(
                survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=contact
            )
        )
        == 1
    )

    # Even a partial (dismissed) response closes it out.
    await _respond(survey_env, survey, contact, completed=False)
    assert (
        await service.deliverable_surveys(
            survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=contact
        )
        == []
    )

    # Another contact — and anonymous visitors — still see it.
    stranger = await make_contact(survey_env.session, survey_env.workspace_id, name="Stranger")
    assert (
        len(
            await service.deliverable_surveys(
                survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=stranger
            )
        )
        == 1
    )
    assert (
        len(
            await service.deliverable_surveys(
                survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=None
            )
        )
        == 1
    )


async def test_frequency_until_completed_tolerates_partials(survey_env):
    survey = await _live_survey(survey_env, frequency={"type": "until_completed"})
    contact = await make_contact(survey_env.session, survey_env.workspace_id)

    await _respond(survey_env, survey, contact, completed=False)
    assert (
        len(
            await service.deliverable_surveys(
                survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=contact
            )
        )
        == 1
    )

    await _respond(survey_env, survey, contact, completed=True)
    assert (
        await service.deliverable_surveys(
            survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=contact
        )
        == []
    )


async def test_frequency_every_time_and_cooldown(survey_env):
    survey = await _live_survey(survey_env, frequency={"type": "every_time"})
    contact = await make_contact(survey_env.session, survey_env.workspace_id)
    await _respond(survey_env, survey, contact, completed=True)
    assert (
        len(
            await service.deliverable_surveys(
                survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=contact
            )
        )
        == 1
    )

    # A cooldown suppresses it until enough time has passed.
    survey.frequency = {"type": "every_time", "cooldown_hours": 72}
    await survey_env.session.flush()
    assert (
        await service.deliverable_surveys(
            survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=contact
        )
        == []
    )

    await _respond(survey_env, survey, contact, completed=True, days_ago=5)
    # Latest response is still today → still suppressed.
    assert (
        await service.deliverable_surveys(
            survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=contact
        )
        == []
    )

    cold = await _live_survey(
        survey_env, name="Cold", frequency={"type": "every_time", "cooldown_hours": 24}
    )
    await _respond(survey_env, cold, contact, completed=True, days_ago=5)
    assert cold.id in {
        s["id"]
        for s in await service.deliverable_surveys(
            survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=contact
        )
    }


async def test_schedule_window(survey_env):
    now = utcnow()
    future = await _live_survey(
        survey_env,
        name="Future",
        schedule={"start_at": (now + timedelta(days=1)).isoformat()},
    )
    past = await _live_survey(
        survey_env, name="Past", schedule={"end_at": (now - timedelta(days=1)).isoformat()}
    )
    current = await _live_survey(
        survey_env,
        name="Now",
        schedule={
            "start_at": (now - timedelta(days=1)).isoformat(),
            "end_at": (now + timedelta(days=1)).isoformat(),
        },
    )

    delivered = {
        s["id"]
        for s in await service.deliverable_surveys(
            survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=None
        )
    }
    assert current.id in delivered
    assert future.id not in delivered
    assert past.id not in delivered


async def test_audience_priority_and_status(survey_env):
    actor = Actor(type="user", id=survey_env.ctx.owner.id)
    low = await _live_survey(survey_env, name="Low", priority=0)
    high = await _live_survey(survey_env, name="High", priority=5)
    await _live_survey(
        survey_env,
        name="Enterprise only",
        audience={
            "type": "filters",
            "filters": [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
        },
    )
    await service.create_survey(
        survey_env.session,
        survey_env.workspace_id,
        actor=actor,
        name="Draft",
        questions=NPS_AND_TEXT,
    )
    await _live_survey(survey_env, name="Manual", trigger={"type": "manual"})

    free = await make_contact(
        survey_env.session, survey_env.workspace_id, attributes={"plan": "free"}
    )
    delivered = await service.deliverable_surveys(
        survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=free
    )
    assert [s["id"] for s in delivered] == [high.id, low.id]

    ent = await make_contact(
        survey_env.session,
        survey_env.workspace_id,
        name="Ent",
        attributes={"plan": "enterprise"},
    )
    ent_names = {
        s["name"]
        for s in await service.deliverable_surveys(
            survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=ent
        )
    }
    assert "Enterprise only" in ent_names

    anon_names = {
        s["name"]
        for s in await service.deliverable_surveys(
            survey_env.session, survey_env.workspace_id, url=INBOX_URL, contact=None
        )
    }
    assert "Enterprise only" not in anon_names


async def test_delivery_is_workspace_scoped(survey_env):
    from app.models.workspace import Workspace

    await _live_survey(survey_env)
    other = Workspace(name="Other", slug="other-survey-ws")
    survey_env.session.add(other)
    await survey_env.session.flush()

    assert (
        await service.deliverable_surveys(survey_env.session, other.id, url=INBOX_URL, contact=None)
        == []
    )
