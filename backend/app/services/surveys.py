"""Survey service: authoring CRUD, publish/pause, widget delivery, append-only
response recording, and the results/NPS math.

Delivery mirrors tours v2: live status, fnmatch URL trigger, schedule window,
audience via `segments.contact_matches`, frequency evaluated against this
contact's previous responses, ordered by priority.

Responses are append-only — a partial submit (`completed=False`) followed by a
full submit stores two rows. Everything rate-like counts `completed` rows only.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow, uuid7
from app.core.errors import ConflictError, NotFoundError, ValidationFailure
from app.core.events import Actor, Event, EventNames, emit
from app.core.pubsub import get_pubsub
from app.models.contact import Contact
from app.models.survey import Survey, SurveyResponse
from app.schemas.surveys import (
    MAX_QUESTIONS,
    NPS_MAX,
    NPS_MIN,
    RATING_MAX,
    RATING_MIN,
    SurveyDayPoint,
    SurveyNpsResult,
    SurveyRatingResult,
    SurveyResults,
    SurveySelectResult,
    SurveyTextAnswer,
    WidgetSurveyOut,
)
from app.services import audit
from app.services import segments as segments_service

DEFAULT_ACCENT = "#6366f1"
DEFAULT_THANKS = "Thanks for the feedback!"
DELIVERY_LIMIT = 5
BY_DAY_WINDOW_DAYS = 30
TEXT_ANSWER_LIMIT = 50
ANALYSIS_ROW_CAP = 5000  # newest N rows feed the distributions
MAX_TEXT_LENGTH = 2000


# ---------------------------------------------------------------------------
# normalization helpers
# ---------------------------------------------------------------------------


def _normalize_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(questions) > MAX_QUESTIONS:
        raise ValidationFailure(f"A survey can hold at most {MAX_QUESTIONS} questions")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for question in questions:
        question_id = str(question.get("id") or uuid7())
        if question_id in seen:
            raise ValidationFailure("Survey question ids must be unique")
        seen.add(question_id)
        qtype = question.get("type") or "text"
        options = list(question.get("options") or []) if qtype == "select" else None
        out.append(
            {
                "id": question_id,
                "type": qtype,
                "question": (question.get("question") or "").strip(),
                "required": bool(question.get("required", True)),
                "options": options,
            }
        )
    return out


def _questions_signature(questions: list[dict[str, Any]]) -> list[str]:
    return [
        json.dumps(
            [q.get("type"), q.get("question"), q.get("required"), q.get("options")],
            sort_keys=True,
            default=str,
        )
        for q in questions
    ]


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _broadcast(workspace_id: str, payload: dict[str, Any]) -> None:
    await get_pubsub().publish(
        f"ws:{workspace_id}", {"type": EventNames.SURVEY_SUBMITTED, "data": payload}
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def get_survey(session: AsyncSession, workspace_id: str, survey_id: str) -> Survey:
    survey = await session.get(Survey, survey_id)
    if survey is None or survey.workspace_id != workspace_id:
        raise NotFoundError("Survey not found")
    return survey


async def list_surveys(session: AsyncSession, workspace_id: str) -> list[Survey]:
    result = await session.execute(
        select(Survey)
        .where(Survey.workspace_id == workspace_id)
        .order_by(Survey.created_at.desc(), Survey.id.desc())
    )
    return list(result.scalars())


async def create_survey(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    name: str,
    questions: list[dict[str, Any]] | None = None,
    presentation: str = "slideout",
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    schedule: dict[str, Any] | None = None,
    frequency: dict[str, Any] | None = None,
    priority: int = 0,
    theme: dict[str, Any] | None = None,
    thanks_message: str | None = None,
) -> Survey:
    survey = Survey(
        workspace_id=workspace_id,
        name=name.strip(),
        status="draft",
        questions=_normalize_questions(questions or []),
        presentation=presentation or "slideout",
        trigger=trigger or {"type": "url_match", "url_pattern": "*"},
        audience=audience or {"type": "all"},
        schedule=schedule or {},
        frequency=frequency or {"type": "once"},
        priority=priority,
        theme=theme or {"accent": DEFAULT_ACCENT},
        thanks_message=thanks_message if thanks_message is not None else DEFAULT_THANKS,
        version=1,
        created_by=actor.id if actor.type == "user" else None,
    )
    session.add(survey)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="survey.create",
        target_type="survey",
        target_id=survey.id,
        meta={"name": survey.name},
    )
    return survey


async def update_survey(
    session: AsyncSession,
    workspace_id: str,
    survey_id: str,
    *,
    actor: Actor,
    name: str | None = None,
    questions: list[dict[str, Any]] | None = None,
    presentation: str | None = None,
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    schedule: dict[str, Any] | None = None,
    frequency: dict[str, Any] | None = None,
    priority: int | None = None,
    theme: dict[str, Any] | None = None,
    thanks_message: str | None = None,
) -> Survey:
    survey = await get_survey(session, workspace_id, survey_id)
    if name is not None:
        survey.name = name.strip()
    if presentation is not None:
        survey.presentation = presentation
    if trigger is not None:
        survey.trigger = trigger
    if audience is not None:
        survey.audience = audience
    if schedule is not None:
        survey.schedule = schedule
    if frequency is not None:
        survey.frequency = frequency
    if priority is not None:
        survey.priority = priority
    if theme is not None:
        survey.theme = theme
    if thanks_message is not None:
        survey.thanks_message = thanks_message
    if questions is not None:
        normalized = _normalize_questions(questions)
        if _questions_signature(normalized) != _questions_signature(survey.questions):
            survey.questions = normalized
            survey.version += 1
        else:
            survey.questions = normalized  # ids may have been (re)assigned
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="survey.update",
        target_type="survey",
        target_id=survey.id,
        meta={"name": survey.name, "version": survey.version},
    )
    return survey


async def delete_survey(
    session: AsyncSession, workspace_id: str, survey_id: str, *, actor: Actor
) -> None:
    survey = await get_survey(session, workspace_id, survey_id)
    name = survey.name
    await session.delete(survey)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="survey.delete",
        target_type="survey",
        target_id=survey_id,
        meta={"name": name},
    )


async def set_status(
    session: AsyncSession, workspace_id: str, survey_id: str, *, actor: Actor, status: str
) -> Survey:
    survey = await get_survey(session, workspace_id, survey_id)
    if status == "live" and not survey.questions:
        raise ConflictError("Add at least one question before publishing")
    survey.status = status
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action=f"survey.{'publish' if status == 'live' else status}",
        target_type="survey",
        target_id=survey.id,
        meta={"status": status},
    )
    return survey


async def publish_survey(
    session: AsyncSession, workspace_id: str, survey_id: str, *, actor: Actor
) -> Survey:
    return await set_status(session, workspace_id, survey_id, actor=actor, status="live")


async def pause_survey(
    session: AsyncSession, workspace_id: str, survey_id: str, *, actor: Actor
) -> Survey:
    return await set_status(session, workspace_id, survey_id, actor=actor, status="paused")


# ---------------------------------------------------------------------------
# answer validation + submission
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def validate_answers(
    questions: list[dict[str, Any]], answers: list[dict[str, Any]], *, completed: bool
) -> list[dict[str, Any]]:
    """Type/range/option check every answer against the question set.

    Blank text answers are dropped (treated as skipped). When `completed` is set
    every required question must carry an answer.
    """
    by_id = {q["id"]: q for q in questions}
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for answer in answers:
        question_id = str(answer.get("question_id") or "")
        question = by_id.get(question_id)
        if question is None:
            raise ValidationFailure(f"Unknown question {question_id!r}")
        if question_id in seen:
            raise ValidationFailure(f"Duplicate answer for question {question_id!r}")
        seen.add(question_id)
        value = answer.get("value")
        qtype = question.get("type")

        if qtype in ("nps", "rating"):
            number = _as_int(value)
            low, high = (NPS_MIN, NPS_MAX) if qtype == "nps" else (RATING_MIN, RATING_MAX)
            if number is None or not low <= number <= high:
                raise ValidationFailure(f"{qtype} answers must be an integer {low}-{high}")
            cleaned.append({"question_id": question_id, "value": number})
        elif qtype == "select":
            options = question.get("options") or []
            if not isinstance(value, str) or value not in options:
                raise ValidationFailure("select answers must be one of the question's options")
            cleaned.append({"question_id": question_id, "value": value})
        else:  # text
            if not isinstance(value, str):
                raise ValidationFailure("text answers must be a string")
            text = value.strip()[:MAX_TEXT_LENGTH]
            if text:
                cleaned.append({"question_id": question_id, "value": text})
            else:
                seen.discard(question_id)

    if completed:
        missing = [q["id"] for q in questions if q.get("required") and q["id"] not in seen]
        if missing:
            raise ValidationFailure("Answer every required question before submitting")
    return cleaned


async def submit_survey_response(
    session: AsyncSession,
    workspace_id: str,
    survey: Survey,
    contact_id: str | None,
    *,
    answers: list[dict[str, Any]],
    completed: bool,
    meta: dict[str, Any] | None = None,
) -> SurveyResponse:
    """Append one (partial or completed) response and fan it out."""
    cleaned = validate_answers(survey.questions, answers or [], completed=completed)
    response = SurveyResponse(
        workspace_id=workspace_id,
        survey_id=survey.id,
        contact_id=contact_id,
        answers=cleaned,
        completed=completed,
        meta=meta or {},
    )
    session.add(response)
    await session.flush()
    payload = {
        "survey_id": survey.id,
        "response_id": response.id,
        "contact_id": contact_id,
        "completed": completed,
        "answers": cleaned,
    }
    await emit(
        session,
        Event(
            name=EventNames.SURVEY_SUBMITTED,
            workspace_id=workspace_id,
            payload=payload,
            actor=Actor(type="contact", id=contact_id) if contact_id else Actor.system(),
        ),
    )
    await _broadcast(workspace_id, payload)
    return response


# ---------------------------------------------------------------------------
# widget delivery
# ---------------------------------------------------------------------------


async def _audience_matches(
    session: AsyncSession, workspace_id: str, audience: dict[str, Any], contact: Contact | None
) -> bool:
    if not audience or audience.get("type") != "filters":
        return True
    filters = audience.get("filters") or []
    if not filters:
        return True
    if contact is None:
        return False
    return await segments_service.contact_matches(session, workspace_id, contact, filters)


def _within_schedule(schedule: dict[str, Any]) -> bool:
    now = utcnow()
    start = _parse_dt((schedule or {}).get("start_at"))
    end = _parse_dt((schedule or {}).get("end_at"))
    if start is not None and now < start:
        return False
    return not (end is not None and now > end)


def _frequency_allows(
    frequency: dict[str, Any], history: tuple[int, bool, datetime] | None
) -> bool:
    """`history` = (responses, any_completed, latest_created_at) for this contact.

    once / until_dismissed → one response (partial counts as dismissed) closes it;
    until_completed → only a completed response closes it;
    every_time → always, honouring `cooldown_hours` against the latest response.
    """
    ftype = (frequency or {}).get("type") or "once"
    if history is None:
        return True
    total, any_completed, latest = history
    if ftype == "every_time":
        cooldown = (frequency or {}).get("cooldown_hours")
        if cooldown:
            return utcnow() - latest >= timedelta(hours=int(cooldown))
        return True
    if ftype == "until_completed":
        return not any_completed
    return total == 0


def widget_payload(survey: Survey) -> dict[str, Any]:
    return WidgetSurveyOut.model_validate(
        {
            "id": survey.id,
            "name": survey.name,
            "questions": survey.questions,
            "presentation": survey.presentation,
            "theme": survey.theme or {},
            "thanks_message": survey.thanks_message,
            "version": survey.version,
            "frequency_type": (survey.frequency or {}).get("type") or "once",
        }
    ).model_dump(mode="json")


async def deliverable_surveys(
    session: AsyncSession,
    workspace_id: str,
    *,
    url: str,
    contact: Contact | None,
) -> list[dict[str, Any]]:
    """Live, in-schedule, URL-matched surveys this contact has not answered
    (per the survey's frequency). Anonymous visitors get everything they target —
    the widget's local seen-set does the rest."""
    result = await session.execute(
        select(Survey)
        .where(Survey.workspace_id == workspace_id, Survey.status == "live")
        .order_by(Survey.priority.desc(), Survey.created_at.asc(), Survey.id.asc())
    )
    surveys = list(result.scalars())
    if not surveys:
        return []

    history: dict[str, tuple[int, bool, datetime]] = {}
    if contact is not None:
        rows = await session.execute(
            select(
                SurveyResponse.survey_id, SurveyResponse.completed, SurveyResponse.created_at
            ).where(
                SurveyResponse.workspace_id == workspace_id,
                SurveyResponse.contact_id == contact.id,
                SurveyResponse.survey_id.in_([s.id for s in surveys]),
            )
        )
        for survey_id, completed, created_at in rows:
            total, any_completed, latest = history.get(survey_id, (0, False, created_at))
            history[survey_id] = (
                total + 1,
                any_completed or bool(completed),
                max(latest, created_at),
            )

    out: list[dict[str, Any]] = []
    for survey in surveys:
        trigger = survey.trigger or {}
        if trigger.get("type") != "url_match":
            continue
        pattern = trigger.get("url_pattern")
        if not pattern or not fnmatch(url, pattern):
            continue
        if not _within_schedule(survey.schedule or {}):
            continue
        if not _frequency_allows(survey.frequency or {}, history.get(survey.id)):
            continue
        if not await _audience_matches(session, workspace_id, survey.audience or {}, contact):
            continue
        out.append(widget_payload(survey))
        if len(out) >= DELIVERY_LIMIT:
            break
    return out


# ---------------------------------------------------------------------------
# responses + results
# ---------------------------------------------------------------------------


async def list_responses(
    session: AsyncSession,
    workspace_id: str,
    survey_id: str,
    *,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[SurveyResponse], int]:
    await get_survey(session, workspace_id, survey_id)
    total = (
        await session.execute(
            select(func.count())
            .select_from(SurveyResponse)
            .where(
                SurveyResponse.workspace_id == workspace_id,
                SurveyResponse.survey_id == survey_id,
            )
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(SurveyResponse)
            .where(
                SurveyResponse.workspace_id == workspace_id,
                SurveyResponse.survey_id == survey_id,
            )
            .order_by(SurveyResponse.created_at.desc(), SurveyResponse.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return list(rows), total


async def compute_results(
    session: AsyncSession, workspace_id: str, survey_id: str
) -> SurveyResults:
    """Totals via SQL; distributions from the newest `ANALYSIS_ROW_CAP` rows.

    NPS/rating/select use COMPLETED responses only (a half-filled card is not a
    verdict); text answers include partials so no written feedback is lost.
    """
    survey = await get_survey(session, workspace_id, survey_id)
    scope = (
        SurveyResponse.workspace_id == workspace_id,
        SurveyResponse.survey_id == survey_id,
    )
    total = (
        await session.execute(select(func.count()).select_from(SurveyResponse).where(*scope))
    ).scalar_one()
    completed_count = (
        await session.execute(
            select(func.count())
            .select_from(SurveyResponse)
            .where(*scope, SurveyResponse.completed.is_(True))
        )
    ).scalar_one()

    rows = list(
        (
            await session.execute(
                select(SurveyResponse)
                .where(*scope)
                .order_by(SurveyResponse.created_at.desc(), SurveyResponse.id.desc())
                .limit(ANALYSIS_ROW_CAP)
            )
        ).scalars()
    )

    # --- by_day (last 30 days, oldest first) -------------------------------
    cutoff = utcnow() - timedelta(days=BY_DAY_WINDOW_DAYS)
    per_day: Counter[str] = Counter()
    for row in rows:
        if row.created_at >= cutoff:
            per_day[row.created_at.date().isoformat()] += 1
    by_day = [SurveyDayPoint(date=day, responses=n) for day, n in sorted(per_day.items())]

    questions = survey.questions
    nps_question = next((q for q in questions if q.get("type") == "nps"), None)
    rating_question = next((q for q in questions if q.get("type") == "rating"), None)
    select_questions = [q for q in questions if q.get("type") == "select"]

    nps_values: list[int] = []
    rating_values: list[int] = []
    select_counts: dict[str, Counter[str]] = {q["id"]: Counter() for q in select_questions}
    text_answers: list[SurveyTextAnswer] = []
    text_question_ids = {q["id"] for q in questions if q.get("type") == "text"}

    for row in rows:  # newest first
        for answer in row.answers or []:
            question_id = answer.get("question_id")
            value = answer.get("value")
            if question_id in text_question_ids and isinstance(value, str):
                if len(text_answers) < TEXT_ANSWER_LIMIT:
                    text_answers.append(
                        SurveyTextAnswer(
                            question_id=str(question_id),
                            value=value,
                            contact_id=row.contact_id,
                            created_at=row.created_at,
                        )
                    )
                continue
            if not row.completed:
                continue
            if nps_question is not None and question_id == nps_question["id"]:
                number = _as_int(value)
                if number is not None:
                    nps_values.append(number)
            elif rating_question is not None and question_id == rating_question["id"]:
                number = _as_int(value)
                if number is not None:
                    rating_values.append(number)
            elif question_id in select_counts and isinstance(value, str):
                select_counts[str(question_id)][value] += 1

    nps: SurveyNpsResult | None = None
    if nps_question is not None and nps_values:
        promoters = sum(1 for v in nps_values if v >= 9)
        passives = sum(1 for v in nps_values if 7 <= v <= 8)
        detractors = sum(1 for v in nps_values if v <= 6)
        n = len(nps_values)
        nps = SurveyNpsResult(
            score=round((promoters / n) * 100 - (detractors / n) * 100),
            promoters=promoters,
            passives=passives,
            detractors=detractors,
        )

    ratings: SurveyRatingResult | None = None
    if rating_question is not None and rating_values:
        distribution = {str(bucket): 0 for bucket in range(RATING_MIN, RATING_MAX + 1)}
        for value in rating_values:
            distribution[str(value)] += 1
        ratings = SurveyRatingResult(
            avg=round(sum(rating_values) / len(rating_values), 2),
            distribution=distribution,
        )

    select_results = [
        SurveySelectResult(
            question_id=q["id"],
            question=q.get("question", ""),
            counts={
                option: select_counts[q["id"]].get(option, 0) for option in q.get("options") or []
            },
        )
        for q in select_questions
    ]

    return SurveyResults(
        responses=total,
        completed=completed_count,
        completion_rate=round(completed_count / total, 4) if total else 0.0,
        by_day=by_day,
        nps=nps,
        ratings=ratings,
        select=select_results,
        text_answers=text_answers,
    )
