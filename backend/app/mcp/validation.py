"""Publish-readiness checks for DAP experiences.

The Pydantic schemas already reject *malformed* input at create/update time.
This module catches the next class of problem, the one that costs a launch:
well-formed content that cannot work — a flow with no steps, a checklist item
pointing at a deleted tour, an audience filter matching zero contacts, a
schedule that ended last week. All of that publishes green and never appears.

Errors mean publishing produces something broken or unreachable. Warnings mean
it will render, but probably not to whom the author intended — they never block,
because "nobody matches this filter yet" is a legitimate state for content
shipped ahead of the audience it targets.

Used by the ``validate_experience`` MCP tool; deliberately free of MCP imports
so the dashboard can call it too.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.checklist import Checklist
from app.models.survey import Survey
from app.models.tour import Tour
from app.services import segments as segments_service

#: Step types that anchor to an element and therefore need a selector.
_ANCHORED_STEP_TYPES = ("tooltip", "hotspot", "action")

#: Tour kinds that render exactly one step.
_SINGLE_STEP_KINDS = ("banner", "announcement")


class Report:
    """Accumulates findings, renders the ``{ok, errors, warnings}`` payload."""

    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []

    def error(self, code: str, message: str, path: str = "") -> None:
        self.errors.append({"code": code, "message": message, "path": path})

    def warn(self, code: str, message: str, path: str = "") -> None:
        self.warnings.append({"code": code, "message": message, "path": path})

    def payload(self, **extra: Any) -> dict[str, Any]:
        return {
            "ok": not self.errors,
            "errors": self.errors,
            "warnings": self.warnings,
            **extra,
        }


def _parse_when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _check_schedule(report: Report, schedule: dict[str, Any] | None) -> None:
    """A window that already closed is an error: publishing it is a no-op the
    author will read as "live"."""
    end_at = _parse_when((schedule or {}).get("end_at"))
    if end_at is not None and end_at <= utcnow():
        report.error(
            "schedule_ended",
            f"schedule.end_at ({end_at.isoformat()}) is in the past — this will never be shown.",
            "schedule.end_at",
        )
    start_at = _parse_when((schedule or {}).get("start_at"))
    if start_at is not None and end_at is not None and start_at >= end_at:
        report.error(
            "schedule_inverted", "schedule.start_at is not before schedule.end_at", "schedule"
        )


async def _check_audience(
    report: Report, session: AsyncSession, workspace_id: str, audience: dict[str, Any] | None
) -> None:
    """Filters that match nobody today: a warning, not an error — content is
    often shipped ahead of the audience it targets."""
    audience = audience or {}
    if audience.get("type") != "filters":
        return
    filters = audience.get("filters") or []
    if not filters:
        report.warn(
            "audience_filters_empty",
            'audience.type is "filters" but no filters are set — this targets everyone.',
            "audience",
        )
        return
    try:
        matched = await segments_service.apply_filters(session, workspace_id, filters)
    except Exception:  # pragma: no cover - a filter the compiler rejects
        report.error("audience_invalid", "audience filters could not be evaluated", "audience")
        return
    if not matched:
        report.warn(
            "audience_matches_none",
            "No contact currently matches these audience filters. If the host page does not "
            "call Stept('identify', …), attribute and email filters can never match.",
            "audience",
        )


async def validate_tour(session: AsyncSession, workspace_id: str, tour: Tour) -> dict[str, Any]:
    report = Report()
    steps: list[dict[str, Any]] = list(tour.steps or [])
    kind = tour.kind or "flow"

    if not steps:
        report.error("no_steps", "A tour needs at least one step.", "steps")
    elif kind in _SINGLE_STEP_KINDS and len(steps) != 1:
        report.error(
            "step_count",
            f"A {kind} renders exactly one step, but this has {len(steps)}.",
            "steps",
        )

    for index, step in enumerate(steps):
        path = f"steps[{index}]"
        step_type = step.get("type") or "tooltip"
        if step_type in _ANCHORED_STEP_TYPES and not (step.get("selector") or "").strip():
            report.error(
                "missing_selector",
                f"{step_type} steps anchor to an element and need a selector.",
                f"{path}.selector",
            )
        if step_type == "action" and not step.get("action"):
            report.error("missing_action", "action steps need an action config.", f"{path}.action")
        if step_type == "wait" and not step.get("wait"):
            report.error("missing_wait", "wait steps need a wait config.", f"{path}.wait")
        if step_type not in ("wait", "action") and not (
            (step.get("title") or "").strip() or (step.get("body") or "").strip()
        ):
            report.error(
                "empty_step",
                "This step renders no text — give it a title or a body.",
                f"{path}.body",
            )
        if (
            step_type in _ANCHORED_STEP_TYPES
            and not step.get("target")
            and not step.get("fallback_selectors")
        ):
            report.warn(
                "brittle_target",
                "No fallback selectors and no captured target — this step cannot self-heal if "
                "the app re-renders. Record the step with browser_record_start to capture one.",
                f"{path}.selector",
            )

    # A tour whose steps span pages needs a url on every one of them, or the
    # player has nowhere to navigate before resolving the anchor.
    with_url = [index for index, step in enumerate(steps) if (step.get("url") or "").strip()]
    if with_url and len(with_url) != len(steps):
        missing = [index for index in range(len(steps)) if index not in set(with_url)]
        report.warn(
            "partial_step_urls",
            "Some steps carry a url and some do not. The player navigates per step, so the "
            f"steps without one ({', '.join(map(str, missing))}) will resolve on whatever page "
            "the visitor happens to be on.",
            "steps",
        )

    _check_schedule(report, tour.schedule)
    await _check_audience(report, session, workspace_id, tour.audience)

    if (tour.trigger or {}).get("type") == "manual":
        referenced = await _tour_is_referenced(session, workspace_id, tour.id)
        if not referenced:
            report.warn(
                "manual_unreferenced",
                "This tour has a manual trigger, so it is never auto-delivered, and no checklist "
                "item starts it. It can only run via Stept('startTour', id), the in-app agent, "
                "or browser_run_tour.",
                "trigger",
            )

    return report.payload(id=tour.id, type="tour", name=tour.name, status=tour.status)


async def _tour_is_referenced(session: AsyncSession, workspace_id: str, tour_id: str) -> bool:
    """Whether any checklist item starts or watches this tour."""
    checklists = (
        (await session.execute(select(Checklist).where(Checklist.workspace_id == workspace_id)))
        .scalars()
        .all()
    )
    for checklist in checklists:
        for item in checklist.items or []:
            if (item.get("action") or {}).get("tour_id") == tour_id:
                return True
            if (item.get("completion") or {}).get("tour_id") == tour_id:
                return True
    return False


async def validate_checklist(
    session: AsyncSession, workspace_id: str, checklist: Checklist
) -> dict[str, Any]:
    report = Report()
    items: list[dict[str, Any]] = list(checklist.items or [])

    if not items:
        report.error("no_items", "A checklist needs at least one item.", "items")
    if not ((checklist.launcher or {}).get("label") or "").strip():
        report.error(
            "no_launcher_label",
            "The launcher has no label — visitors get an unlabelled pill.",
            "launcher.label",
        )

    referenced_ids = {
        tour_id
        for item in items
        for tour_id in (
            (item.get("action") or {}).get("tour_id"),
            (item.get("completion") or {}).get("tour_id"),
        )
        if tour_id
    }
    known: set[str] = set()
    if referenced_ids:
        known = set(
            (
                await session.execute(
                    select(Tour.id).where(
                        Tour.workspace_id == workspace_id, Tour.id.in_(referenced_ids)
                    )
                )
            )
            .scalars()
            .all()
        )

    for index, item in enumerate(items):
        path = f"items[{index}]"
        action = item.get("action") or {}
        completion = item.get("completion") or {}
        for label, config in (("action", action), ("completion", completion)):
            tour_id = config.get("tour_id")
            if tour_id and tour_id not in known:
                report.error(
                    "missing_tour",
                    f"{label}.tour_id {tour_id} does not exist in this workspace.",
                    f"{path}.{label}.tour_id",
                )
        if (
            action.get("type") == "start_tour"
            and completion.get("type") == "tour_completed"
            and action.get("tour_id") != completion.get("tour_id")
        ):
            report.warn(
                "completion_tour_mismatch",
                "This item starts one tour but ticks off when a different one completes — "
                "visitors will do the task and watch the box stay unchecked.",
                f"{path}.completion.tour_id",
            )
        if action.get("type") == "none" and completion.get("type") == "manual":
            report.warn(
                "inert_item",
                "This item has no action and only manual completion — it is a label, not a task.",
                path,
            )

    await _check_audience(report, session, workspace_id, checklist.audience)
    return report.payload(
        id=checklist.id, type="checklist", name=checklist.name, status=checklist.status
    )


async def validate_survey(
    session: AsyncSession, workspace_id: str, survey: Survey
) -> dict[str, Any]:
    report = Report()
    questions: list[dict[str, Any]] = list(survey.questions or [])

    if not questions:
        report.error("no_questions", "A survey needs at least one question.", "questions")

    for index, question in enumerate(questions):
        path = f"questions[{index}]"
        if not (question.get("question") or "").strip():
            report.error("empty_question", "Question text is empty.", f"{path}.question")
        if question.get("type") == "select":
            options = [option for option in (question.get("options") or []) if option.strip()]
            if len(options) < 2:
                report.error(
                    "select_options",
                    "select questions need at least two non-blank options.",
                    f"{path}.options",
                )
            if len(set(options)) != len(options):
                report.error(
                    "select_duplicates", "select options must be unique.", f"{path}.options"
                )

    _check_schedule(report, survey.schedule)
    await _check_audience(report, session, workspace_id, survey.audience)

    if (survey.trigger or {}).get("type") == "manual":
        report.warn(
            "manual_trigger",
            "This survey has a manual trigger, so it is never delivered automatically.",
            "trigger",
        )

    return report.payload(id=survey.id, type="survey", name=survey.name, status=survey.status)
