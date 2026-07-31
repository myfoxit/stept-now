"""Surveys API (dashboard authoring + results).

The empty router is pre-registered under /w/{workspace_id}; routes live here.
Surveys are a DAP experience, so they reuse the tour permissions:
reads require tours:read, mutations require tours:manage.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.pagination import OffsetPage, clamp_limit
from app.core.permissions import Perm
from app.schemas.common import Msg
from app.schemas.surveys import (
    SurveyCreate,
    SurveyOut,
    SurveyResponseOut,
    SurveyResults,
    SurveyUpdate,
)
from app.services import surveys as surveys_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


@router.get(
    "/surveys",
    response_model=list[SurveyOut],
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def list_surveys(principal: Member, session: Db):
    rows = await surveys_service.list_surveys(session, principal.workspace.id)
    return [SurveyOut.model_validate(s) for s in rows]


@router.post(
    "/surveys",
    response_model=SurveyOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def create_survey(body: SurveyCreate, principal: Member, session: Db):
    survey = await surveys_service.create_survey(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        name=body.name,
        questions=[q.model_dump() for q in body.questions],
        presentation=body.presentation,
        trigger=body.trigger.model_dump(),
        audience=body.audience.model_dump(),
        schedule=body.schedule.model_dump(mode="json", exclude_none=True),
        frequency=body.frequency.model_dump(exclude_none=True),
        priority=body.priority,
        theme=body.theme.model_dump(),
        thanks_message=body.thanks_message,
    )
    return SurveyOut.model_validate(survey)


@router.get(
    "/surveys/{survey_id}",
    response_model=SurveyOut,
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def get_survey(survey_id: str, principal: Member, session: Db):
    survey = await surveys_service.get_survey(session, principal.workspace.id, survey_id)
    return SurveyOut.model_validate(survey)


@router.patch(
    "/surveys/{survey_id}",
    response_model=SurveyOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def update_survey(survey_id: str, body: SurveyUpdate, principal: Member, session: Db):
    survey = await surveys_service.update_survey(
        session,
        principal.workspace.id,
        survey_id,
        actor=_actor(principal),
        name=body.name,
        questions=[q.model_dump() for q in body.questions] if body.questions is not None else None,
        presentation=body.presentation,
        trigger=body.trigger.model_dump() if body.trigger is not None else None,
        audience=body.audience.model_dump() if body.audience is not None else None,
        schedule=(
            body.schedule.model_dump(mode="json", exclude_none=True)
            if body.schedule is not None
            else None
        ),
        frequency=(
            body.frequency.model_dump(exclude_none=True) if body.frequency is not None else None
        ),
        priority=body.priority,
        theme=body.theme.model_dump() if body.theme is not None else None,
        thanks_message=body.thanks_message,
    )
    return SurveyOut.model_validate(survey)


@router.delete(
    "/surveys/{survey_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def delete_survey(survey_id: str, principal: Member, session: Db):
    await surveys_service.delete_survey(
        session, principal.workspace.id, survey_id, actor=_actor(principal)
    )
    return Msg(message="Survey deleted")


@router.post(
    "/surveys/{survey_id}/publish",
    response_model=SurveyOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def publish_survey(survey_id: str, principal: Member, session: Db):
    survey = await surveys_service.publish_survey(
        session, principal.workspace.id, survey_id, actor=_actor(principal)
    )
    return SurveyOut.model_validate(survey)


@router.post(
    "/surveys/{survey_id}/pause",
    response_model=SurveyOut,
    dependencies=[Depends(require_perm(Perm.TOURS_MANAGE))],
)
async def pause_survey(survey_id: str, principal: Member, session: Db):
    survey = await surveys_service.pause_survey(
        session, principal.workspace.id, survey_id, actor=_actor(principal)
    )
    return SurveyOut.model_validate(survey)


@router.get(
    "/surveys/{survey_id}/results",
    response_model=SurveyResults,
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def survey_results(survey_id: str, principal: Member, session: Db):
    return await surveys_service.compute_results(session, principal.workspace.id, survey_id)


@router.get(
    "/surveys/{survey_id}/responses",
    response_model=OffsetPage[SurveyResponseOut],
    dependencies=[Depends(require_perm(Perm.TOURS_READ))],
)
async def list_survey_responses(
    survey_id: str,
    principal: Member,
    session: Db,
    limit: int | None = Query(None, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    limit = clamp_limit(limit, default=25)
    rows, total = await surveys_service.list_responses(
        session, principal.workspace.id, survey_id, limit=limit, offset=offset
    )
    return OffsetPage[SurveyResponseOut](
        items=[SurveyResponseOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
