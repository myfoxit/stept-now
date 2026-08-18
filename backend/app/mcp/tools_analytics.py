"""Adoption analytics MCP tools — the readout half of "build it with AI".

Authoring without measurement is half a product: the same connection that
creates a flow should be able to answer "did it work, and where do people fall
out". These wrap the existing stats services and add the two things a *readout*
needs that a dashboard does not: the funnel already differenced into per-step
drop-off with the worst step named, and a single cross-content ranking so
"which onboarding is underperforming" is one call rather than N.

Read-only throughout — they need ``reports:read``, never ``tours:manage``, so a
key handed to an analyst cannot edit anything.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import AppError
from app.core.permissions import Perm
from app.mcp.annotations import READ_ONLY
from app.mcp.auth import (
    authorization_error,
    open_session,
    permission_error,
    resolve_request_key,
)
from app.mcp.server import mcp
from app.models.checklist import Checklist, ChecklistProgress
from app.models.survey import Survey, SurveyResponse
from app.models.tour import Tour, TourEvent
from app.services import checklists as checklists_service
from app.services import surveys as surveys_service
from app.services import tours as tours_service

#: Default lookback for the overview. Matches the stats services' own window.
DEFAULT_WINDOW_DAYS = 30

#: Cap on rows returned by the overview, so a large workspace cannot blow the
#: caller's context in one call.
OVERVIEW_LIMIT = 50


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _funnel(steps: list[dict[str, Any]], starts: int) -> dict[str, Any]:
    """Turn per-step view counts into a funnel a reader can act on.

    `drop_off` on the stats rows is already "viewed this step but not the next";
    what a readout needs on top is each step's share of the people who *started*
    and an explicit pointer at the worst one, so the recommendation writes
    itself.
    """
    rows: list[dict[str, Any]] = []
    for step in steps:
        viewed = int(step.get("viewed") or 0)
        dropped = int(step.get("drop_off") or 0)
        rows.append(
            {
                "index": step.get("index"),
                "title": step.get("title") or "",
                "viewed": viewed,
                "drop_off": dropped,
                "reach_rate": _rate(viewed, starts),
                "drop_off_rate": _rate(dropped, viewed),
                "healed": int(step.get("healed") or 0),
            }
        )
    worst = max(rows, key=lambda row: row["drop_off"], default=None)
    return {
        "steps": rows,
        "biggest_drop_off": (
            None
            if worst is None or worst["drop_off"] == 0
            else {
                "index": worst["index"],
                "title": worst["title"],
                "lost": worst["drop_off"],
                "of_viewers": worst["viewed"],
                "rate": worst["drop_off_rate"],
            }
        ),
    }


@mcp.tool(annotations=READ_ONLY)
async def get_tour_analytics(tour_id: str) -> dict[str, Any]:
    """Performance of one tour: starts, completions, completion rate, the
    step-by-step funnel with per-step drop-off, and daily starts/completions.

    `biggest_drop_off` names the step that loses the most people, which is
    almost always the answer to "where should I change the copy". Playback
    health (`step_error` / `step_blocked` / self-healed views) comes back too —
    a bad completion rate is often a broken selector rather than bad content,
    and those are different fixes.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.REPORTS_READ):
            return permission_error(Perm.REPORTS_READ)
        try:
            tour = await tours_service.get_tour(session, key.workspace_id, tour_id)
            stats = await tours_service.compute_stats(session, key.workspace_id, tour_id)
            health = await tours_service.tour_health(session, key.workspace_id, tour)
        except AppError as exc:
            return {"error": exc.message}
        data = stats.model_dump(mode="json")
        return {
            "id": tour.id,
            "name": tour.name,
            "kind": tour.kind,
            "status": tour.status,
            "starts": data["starts"],
            "unique_starts": data["unique_starts"],
            "completions": data["completions"],
            "dismissals": data["dismissals"],
            "completion_rate": data["completion_rate"],
            "funnel": _funnel(data["steps"], data["starts"]),
            "by_day": data["by_day"],
            "playback_health": {
                "health": health["health"],
                "step_errors": health["step_errors"],
                "step_blocked": health["step_blocked"],
                "healed_step_views": health["healed_step_views"],
                "broken_steps": health["broken_steps"],
                "blocked_steps": health["blocked_steps"],
            },
        }


@mcp.tool(annotations=READ_ONLY)
async def get_checklist_analytics(checklist_id: str) -> dict[str, Any]:
    """Per-item completion for one checklist: how many visitors opened it, how
    many finished it, and how many completed each individual item.

    The item with the steepest fall from the item above it is where activation
    stalls — that is the friction report.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.REPORTS_READ):
            return permission_error(Perm.REPORTS_READ)
        try:
            checklist = await checklists_service.get_checklist(
                session, key.workspace_id, checklist_id
            )
            stats = await checklists_service.compute_stats(session, key.workspace_id, checklist_id)
        except AppError as exc:
            return {"error": exc.message}
        data = stats.model_dump(mode="json")
        starts = int(data["starts"])
        items: list[dict[str, Any]] = []
        previous: int | None = None
        for item in data["items"]:
            completed = int(item["completed_count"])
            items.append(
                {
                    "id": item["id"],
                    "title": item["title"],
                    "completed": completed,
                    "completion_rate": _rate(completed, starts),
                    "lost_from_previous": (
                        None if previous is None else max(previous - completed, 0)
                    ),
                }
            )
            previous = completed
        stalls_at = max(
            (item for item in items if item["lost_from_previous"]),
            key=lambda item: item["lost_from_previous"],
            default=None,
        )
        return {
            "id": checklist.id,
            "name": checklist.name,
            "status": checklist.status,
            "starts": starts,
            "completions": data["completions"],
            "completion_rate": data["completion_rate"],
            "items": items,
            "stalls_at": (
                None
                if stalls_at is None
                else {
                    "id": stalls_at["id"],
                    "title": stalls_at["title"],
                    "lost": stalls_at["lost_from_previous"],
                }
            ),
        }


@mcp.tool(annotations=READ_ONLY)
async def get_survey_results(survey_id: str) -> dict[str, Any]:
    """Readout for one survey: response and completion counts, NPS breakdown,
    rating distribution, per-option select counts, and the free-text answers.

    NPS / rating / select are computed from COMPLETED responses only — a
    half-filled card is not a verdict. Text answers include partials so no
    written feedback is lost, which makes them the right input for theme
    clustering.
    """
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.REPORTS_READ):
            return permission_error(Perm.REPORTS_READ)
        try:
            survey = await surveys_service.get_survey(session, key.workspace_id, survey_id)
            results = await surveys_service.compute_results(session, key.workspace_id, survey_id)
        except AppError as exc:
            return {"error": exc.message}
        return {
            "id": survey.id,
            "name": survey.name,
            "status": survey.status,
            "questions": [
                {"id": q.get("id"), "type": q.get("type"), "question": q.get("question")}
                for q in survey.questions or []
            ],
            **results.model_dump(mode="json"),
        }


async def _tour_reach(
    session: AsyncSession, workspace_id: str, since: Any
) -> dict[str, dict[str, int]]:
    rows = await session.execute(
        select(TourEvent.tour_id, TourEvent.event, func.count())
        .where(
            TourEvent.workspace_id == workspace_id,
            TourEvent.created_at >= since,
            TourEvent.event.in_(("started", "completed", "dismissed")),
        )
        .group_by(TourEvent.tour_id, TourEvent.event)
    )
    out: dict[str, dict[str, int]] = {}
    for tour_id, event, count in rows.all():
        out.setdefault(tour_id, {})[event] = int(count)
    return out


@mcp.tool(annotations=READ_ONLY)
async def get_adoption_overview(days: int = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """Every tour, checklist and survey ranked by reach in ONE call — the
    portfolio view.

    Start here for "how is onboarding doing" or "which experience should I fix
    first", then deep-dive the worst rows with `get_tour_analytics` /
    `get_checklist_analytics` / `get_survey_results`. `days` defaults to 30.

    Rows are sorted by reach descending and capped at 50. Draft and paused
    content is included with its status, because "nobody sees it" is usually the
    finding.
    """
    if days < 1 or days > 365:
        return {"error": "days must be between 1 and 365"}
    async with open_session() as session:
        key = await resolve_request_key(session)
        if key is None:
            return authorization_error()
        if not key.has(Perm.REPORTS_READ):
            return permission_error(Perm.REPORTS_READ)
        since = utcnow() - timedelta(days=days)
        workspace_id = key.workspace_id

        rows: list[dict[str, Any]] = []

        tours = (
            (await session.execute(select(Tour).where(Tour.workspace_id == workspace_id)))
            .scalars()
            .all()
        )
        reach = await _tour_reach(session, workspace_id, since)
        for tour in tours:
            counts = reach.get(tour.id, {})
            starts = counts.get("started", 0)
            completions = counts.get("completed", 0)
            rows.append(
                {
                    "id": tour.id,
                    "type": "tour",
                    "kind": tour.kind,
                    "name": tour.name,
                    "status": tour.status,
                    "reach": starts,
                    "completions": completions,
                    "completion_rate": _rate(completions, starts),
                    "dismissals": counts.get("dismissed", 0),
                }
            )

        checklists = (
            (await session.execute(select(Checklist).where(Checklist.workspace_id == workspace_id)))
            .scalars()
            .all()
        )
        progress_rows = await session.execute(
            select(
                ChecklistProgress.checklist_id,
                func.count(),
                func.count(ChecklistProgress.completed_at),
            )
            .where(
                ChecklistProgress.workspace_id == workspace_id,
                ChecklistProgress.created_at >= since,
            )
            .group_by(ChecklistProgress.checklist_id)
        )
        progress = {
            checklist_id: (int(total), int(done))
            for checklist_id, total, done in progress_rows.all()
        }
        for checklist in checklists:
            total, done = progress.get(checklist.id, (0, 0))
            rows.append(
                {
                    "id": checklist.id,
                    "type": "checklist",
                    "name": checklist.name,
                    "status": checklist.status,
                    "reach": total,
                    "completions": done,
                    "completion_rate": _rate(done, total),
                }
            )

        surveys = (
            (await session.execute(select(Survey).where(Survey.workspace_id == workspace_id)))
            .scalars()
            .all()
        )
        response_rows = await session.execute(
            select(
                SurveyResponse.survey_id,
                func.count(),
                # SUM(CASE …) rather than a cast: portable across SQLite and PG,
                # where boolean→int casting differs.
                func.sum(case((SurveyResponse.completed.is_(True), 1), else_=0)),
            )
            .where(
                SurveyResponse.workspace_id == workspace_id,
                SurveyResponse.created_at >= since,
            )
            .group_by(SurveyResponse.survey_id)
        )
        responses = {
            survey_id: (int(total), int(done or 0))
            for survey_id, total, done in response_rows.all()
        }
        for survey in surveys:
            total, done = responses.get(survey.id, (0, 0))
            rows.append(
                {
                    "id": survey.id,
                    "type": "survey",
                    "name": survey.name,
                    "status": survey.status,
                    "reach": total,
                    "completions": done,
                    "completion_rate": _rate(done, total),
                }
            )

        rows.sort(key=lambda row: (-row["reach"], row["name"]))
        truncated = len(rows) > OVERVIEW_LIMIT
        return {
            "window_days": days,
            "since": since.isoformat(),
            "total": len(rows),
            "truncated": truncated,
            "content": rows[:OVERVIEW_LIMIT],
        }
