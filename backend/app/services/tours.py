"""Tour service: authoring CRUD, publish/pause, delivery v2 (schedule/frequency/
priority), funnel stats v2, telemetry recording (+ realtime broadcast), and the
recorder/extension token flows.

Delivery evaluates audience filters per contact via `segments.contact_matches`
(no workspace scans) and batches TourEvent lookups (one grouped query for all
candidate tours); URL triggers use fnmatch-style `*` globbing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow, uuid7
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.core.events import Actor, Event, EventNames, emit
from app.core.permissions import Perm, resolve_permissions
from app.core.security import create_recorder_token, decode_token
from app.models.contact import Contact
from app.models.tour import Tour, TourEvent
from app.models.workspace import Membership
from app.realtime.manager import broadcast, workspace_topic
from app.schemas.tours import (
    TourDayStat,
    TourSettings,
    TourStats,
    TourStepOut,
    TourStepStat,
    TourTheme,
    WidgetTourOut,
)
from app.services import audit
from app.services import segments as segments_service

_FINISHED_EVENTS = ("completed", "dismissed")
#: The playback lifecycle vocabulary the widget emits. Ingestion is tolerant:
#: anything else is accepted and IGNORED (a newer widget must never 422 an
#: older backend, and a hostile page must not fill telemetry with junk types).
KNOWN_TOUR_EVENTS = frozenset(
    {"started", "step_viewed", "step_blocked", "completed", "dismissed", "step_error"}
)
DEFAULT_ACCENT = "#6366f1"
DEFAULT_FREQUENCY = {"type": "until_dismissed"}
MAX_DELIVERED = 5  # widget bootstrap cap
STATS_WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# normalization helpers
# ---------------------------------------------------------------------------


def _norm_media(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value or not value.get("url"):
        return None
    return {"type": value.get("type") or "image", "url": value["url"]}


def _norm_advance(value: dict[str, Any] | None) -> dict[str, Any]:
    value = value or {}
    return {"on": value.get("on") or "button", "delay_ms": value.get("delay_ms")}


def _norm_cta(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """A CTA with neither a label nor a URL is indistinguishable from none —
    collapse it so an empty form row cannot bump the tour version."""
    if not value:
        return None
    label = (value.get("label") or "").strip()
    url = (value.get("url") or "").strip() or None
    if not label and not url:
        return None
    return {"label": label, "url": url}


def _norm_action(value: dict[str, Any] | None, step_type: str) -> dict[str, Any] | None:
    if step_type != "action" or not value:
        return None
    return {
        "kind": value.get("kind") or "click",
        "value": value.get("value"),
        "url": value.get("url"),
    }


def _norm_wait(value: dict[str, Any] | None, step_type: str) -> dict[str, Any] | None:
    if step_type != "wait" or not value:
        return None
    return {
        "for": value.get("for") or value.get("for_") or "element",
        "selector": value.get("selector"),
        "url_pattern": value.get("url_pattern"),
        "timeout_ms": int(value.get("timeout_ms") or 10_000),
    }


def _normalize_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every step a stable id and the full v2 field set."""
    out: list[dict[str, Any]] = []
    for step in steps:
        step_type = step.get("type") or "tooltip"
        out.append(
            {
                "id": step.get("id") or uuid7(),
                "type": step_type,
                "selector": (step.get("selector") or "").strip(),
                "fallback_selectors": [s for s in (step.get("fallback_selectors") or []) if s][:5],
                "text_hint": (step.get("text_hint") or "").strip()[:80],
                "target": step.get("target") or None,
                "title": step.get("title") or "",
                "body": step.get("body") or "",
                "media": _norm_media(step.get("media")),
                "screenshot_key": step.get("screenshot_key") or None,
                "sandbox_key": step.get("sandbox_key") or None,
                "placement": step.get("placement") or "auto",
                "advance": _norm_advance(step.get("advance")),
                "cta": _norm_cta(step.get("cta")),
                "secondary_cta": _norm_cta(step.get("secondary_cta")),
                "action": _norm_action(step.get("action"), step_type),
                "wait": _norm_wait(step.get("wait"), step_type),
            }
        )
    return out


# Content fields participate in the version bump; `target`, `screenshot_key` and
# `sandbox_key` are re-capture artifacts and must NOT invalidate in-flight
# playback — re-recording a screen should not restart everyone mid-tour.
_CONTENT_FIELDS = (
    "type",
    "selector",
    "fallback_selectors",
    "text_hint",
    "title",
    "body",
    "media",
    "placement",
    "advance",
    "cta",
    "secondary_cta",
    "action",
    "wait",
)
_ALL_FIELDS = (*_CONTENT_FIELDS, "target", "screenshot_key", "sandbox_key")


def _signature(steps: list[dict[str, Any]], fields: tuple[str, ...]) -> list[str]:
    """Content fingerprint (ignores ids) used to decide if steps changed."""
    return [
        json.dumps({f: step.get(f) for f in fields}, sort_keys=True, default=str) for step in steps
    ]


def _apply_steps(tour: Tour, steps: list[dict[str, Any]]) -> None:
    """Persist normalized steps; bump the version only when *content* changed.

    target/screenshot_key-only edits persist without a bump; a byte-identical
    payload leaves the stored steps (and their ids) untouched."""
    normalized = _normalize_steps(steps)
    current = tour.steps or []
    if _signature(normalized, _ALL_FIELDS) == _signature(current, _ALL_FIELDS):
        return
    if _signature(normalized, _CONTENT_FIELDS) != _signature(current, _CONTENT_FIELDS):
        tour.version += 1
    tour.steps = normalized


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def get_tour(session: AsyncSession, workspace_id: str, tour_id: str) -> Tour:
    tour = await session.get(Tour, tour_id)
    if tour is None or tour.workspace_id != workspace_id:
        raise NotFoundError("Tour not found")
    return tour


async def list_tours(session: AsyncSession, workspace_id: str) -> list[Tour]:
    result = await session.execute(
        select(Tour)
        .where(Tour.workspace_id == workspace_id)
        .order_by(Tour.created_at.desc(), Tour.id.desc())
    )
    return list(result.scalars())


async def create_tour(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    name: str,
    description: str = "",
    kind: str = "flow",
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    schedule: dict[str, Any] | None = None,
    frequency: dict[str, Any] | None = None,
    priority: int = 0,
    settings: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    theme: dict[str, Any] | None = None,
) -> Tour:
    tour = Tour(
        workspace_id=workspace_id,
        name=name.strip(),
        description=description or "",
        kind=kind or "flow",
        status="draft",
        trigger=trigger or {"type": "manual"},
        audience=audience or {"type": "all"},
        schedule=schedule or {},
        frequency=frequency or dict(DEFAULT_FREQUENCY),
        priority=priority,
        settings=settings or {},
        steps=_normalize_steps(steps or []),
        theme=theme or {"accent": DEFAULT_ACCENT},
        version=1,
        created_by=actor.id if actor.type == "user" else None,
    )
    session.add(tour)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="tour.create",
        target_type="tour",
        target_id=tour.id,
        meta={"name": tour.name, "kind": tour.kind},
    )
    return tour


async def update_tour(
    session: AsyncSession,
    workspace_id: str,
    tour_id: str,
    *,
    actor: Actor,
    name: str | None = None,
    description: str | None = None,
    kind: str | None = None,
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    schedule: dict[str, Any] | None = None,
    frequency: dict[str, Any] | None = None,
    priority: int | None = None,
    settings: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    theme: dict[str, Any] | None = None,
) -> Tour:
    tour = await get_tour(session, workspace_id, tour_id)
    if name is not None:
        tour.name = name.strip()
    if description is not None:
        tour.description = description
    if kind is not None:
        tour.kind = kind
    if trigger is not None:
        tour.trigger = trigger
    if audience is not None:
        tour.audience = audience
    if schedule is not None:
        tour.schedule = schedule
    if frequency is not None:
        tour.frequency = frequency
    if priority is not None:
        tour.priority = priority
    if settings is not None:
        tour.settings = settings
    if theme is not None:
        tour.theme = theme
    if steps is not None:
        _apply_steps(tour, steps)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="tour.update",
        target_type="tour",
        target_id=tour.id,
        meta={"name": tour.name, "version": tour.version},
    )
    return tour


async def delete_tour(
    session: AsyncSession, workspace_id: str, tour_id: str, *, actor: Actor
) -> None:
    tour = await get_tour(session, workspace_id, tour_id)
    await session.delete(tour)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="tour.delete",
        target_type="tour",
        target_id=tour_id,
        meta={"name": tour.name},
    )


async def set_status(
    session: AsyncSession, workspace_id: str, tour_id: str, *, actor: Actor, status: str
) -> Tour:
    tour = await get_tour(session, workspace_id, tour_id)
    tour.status = status
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action=f"tour.{'publish' if status == 'live' else status}",
        target_type="tour",
        target_id=tour.id,
        meta={"status": status},
    )
    return tour


async def publish_tour(
    session: AsyncSession, workspace_id: str, tour_id: str, *, actor: Actor
) -> Tour:
    return await set_status(session, workspace_id, tour_id, actor=actor, status="live")


async def pause_tour(
    session: AsyncSession, workspace_id: str, tour_id: str, *, actor: Actor
) -> Tour:
    return await set_status(session, workspace_id, tour_id, actor=actor, status="paused")


# ---------------------------------------------------------------------------
# extension-facing mutations (draft create, steps PUT, meta PATCH)
# ---------------------------------------------------------------------------


async def create_extension_draft(
    session: AsyncSession,
    workspace_id: str,
    *,
    user_id: str,
    name: str,
    url_pattern: str | None,
    steps: list[dict[str, Any]],
) -> Tour:
    """Draft tour from the recorder/extension: default titles, url_match trigger."""
    built = _normalize_steps(steps)
    for i, step in enumerate(built):
        if not step["title"]:
            step["title"] = f"Step {i + 1}"
    trigger = (
        {"type": "url_match", "url_pattern": url_pattern} if url_pattern else {"type": "manual"}
    )
    tour = Tour(
        workspace_id=workspace_id,
        name=name.strip(),
        description="",
        kind="flow",
        status="draft",
        trigger=trigger,
        audience={"type": "all"},
        schedule={},
        frequency=dict(DEFAULT_FREQUENCY),
        priority=0,
        settings={},
        steps=built,
        theme={"accent": DEFAULT_ACCENT},
        version=1,
        created_by=user_id,
    )
    session.add(tour)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=Actor(type="user", id=user_id),
        action="tour.recorder_create",
        target_type="tour",
        target_id=tour.id,
        meta={"name": tour.name, "steps": len(built)},
    )
    return tour


async def update_steps(
    session: AsyncSession,
    workspace_id: str,
    tour_id: str,
    *,
    actor: Actor,
    steps: list[dict[str, Any]],
    base_version: int,
) -> Tour:
    """Replace steps optimistically: 409 when the tour moved past base_version
    (extension edits must not clobber concurrent dashboard edits)."""
    tour = await get_tour(session, workspace_id, tour_id)
    if tour.version != base_version:
        raise ConflictError(
            "Tour was modified elsewhere — reload before saving",
            details={"current_version": tour.version, "base_version": base_version},
        )
    _apply_steps(tour, steps)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="tour.update",
        target_type="tour",
        target_id=tour.id,
        meta={"name": tour.name, "version": tour.version, "via": "extension"},
    )
    return tour


async def patch_draft_meta(
    session: AsyncSession,
    workspace_id: str,
    tour_id: str,
    *,
    actor: Actor,
    name: str | None = None,
    url_pattern: str | None = None,
    url_pattern_set: bool = False,
) -> Tour:
    """Rename / retrigger a draft from the extension. Live tours are read-only
    there (409) — pausing/editing published content is a dashboard decision."""
    tour = await get_tour(session, workspace_id, tour_id)
    if tour.status == "live":
        raise ConflictError("Tour is live — edit it from the dashboard")
    if name is not None:
        tour.name = name.strip()
    if url_pattern_set:
        tour.trigger = (
            {"type": "url_match", "url_pattern": url_pattern} if url_pattern else {"type": "manual"}
        )
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="tour.update",
        target_type="tour",
        target_id=tour.id,
        meta={"name": tour.name, "via": "extension"},
    )
    return tour


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


async def compute_stats(session: AsyncSession, workspace_id: str, tour_id: str) -> TourStats:
    tour = await get_tour(session, workspace_id, tour_id)
    scoped = (TourEvent.workspace_id == workspace_id, TourEvent.tour_id == tour_id)

    count_rows = await session.execute(
        select(TourEvent.event, func.count()).where(*scoped).group_by(TourEvent.event)
    )
    counts: dict[str, int] = {event: count for event, count in count_rows.all()}
    starts = counts.get("started", 0)
    completions = counts.get("completed", 0)
    dismissals = counts.get("dismissed", 0)
    step_errors = counts.get("step_error", 0)
    step_blocked = counts.get("step_blocked", 0)

    # Unique starts: distinct known contacts + each anonymous start counted once.
    distinct_contacts, anonymous_starts = (
        await session.execute(
            select(
                func.count(func.distinct(TourEvent.contact_id)),
                func.count(TourEvent.id).filter(TourEvent.contact_id.is_(None)),
            ).where(*scoped, TourEvent.event == "started")
        )
    ).one()
    unique_starts = int(distinct_contacts or 0) + int(anonymous_starts or 0)

    step_count = len(tour.steps or [])
    viewed = [0] * step_count
    healed = [0] * step_count
    viewed_rows = await session.execute(
        select(TourEvent.step_index, func.count())
        .where(*scoped, TourEvent.event == "step_viewed", TourEvent.step_index.is_not(None))
        .group_by(TourEvent.step_index)
    )
    for index, count in viewed_rows.all():
        if 0 <= index < step_count:
            viewed[index] = count
    healed_rows = await session.execute(
        select(TourEvent.step_index, func.count())
        .where(
            *scoped,
            TourEvent.event == "step_viewed",
            TourEvent.step_index.is_not(None),
            TourEvent.meta["healed"].as_boolean().is_(True),
        )
        .group_by(TourEvent.step_index)
    )
    for index, count in healed_rows.all():
        if 0 <= index < step_count:
            healed[index] = count

    by_day = await _stats_by_day(session, scoped)

    step_stats: list[TourStepStat] = []
    for i, step in enumerate(tour.steps or []):
        # Drop-off = viewers of this step who did not reach the next step (or, for
        # the final step, who did not complete).
        nxt = viewed[i + 1] if i + 1 < step_count else completions
        step_stats.append(
            TourStepStat(
                index=i,
                title=step.get("title", ""),
                viewed=viewed[i],
                drop_off=max(viewed[i] - nxt, 0),
                healed=healed[i],
            )
        )

    return TourStats(
        starts=starts,
        completions=completions,
        dismissals=dismissals,
        completion_rate=round(completions / starts, 4) if starts else 0.0,
        unique_starts=unique_starts,
        step_errors=step_errors,
        step_blocked=step_blocked,
        by_day=by_day,
        steps=step_stats,
    )


async def _stats_by_day(session: AsyncSession, scoped: tuple[Any, ...]) -> list[TourDayStat]:
    """starts/completions per day over the trailing window, zero-filled."""
    today = utcnow().date()
    first_day = today - timedelta(days=STATS_WINDOW_DAYS - 1)
    window_start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=UTC)
    day = func.date(TourEvent.created_at)
    rows = await session.execute(
        select(day, TourEvent.event, func.count())
        .where(
            *scoped,
            TourEvent.event.in_(("started", "completed")),
            TourEvent.created_at >= window_start,
        )
        .group_by(day, TourEvent.event)
    )
    per_day: dict[str, dict[str, int]] = {}
    for value, event, count in rows.all():
        per_day.setdefault(str(value), {})[event] = count
    out: list[TourDayStat] = []
    for offset in range(STATS_WINDOW_DAYS):
        date = (first_day + timedelta(days=offset)).isoformat()
        bucket = per_day.get(date, {})
        out.append(
            TourDayStat(
                date=date,
                starts=bucket.get("started", 0),
                completions=bucket.get("completed", 0),
            )
        )
    return out


async def list_events(
    session: AsyncSession, workspace_id: str, tour_id: str, *, limit: int, offset: int
) -> tuple[list[TourEvent], int]:
    """Newest-first telemetry page for the analytics table (tour must exist)."""
    await get_tour(session, workspace_id, tour_id)
    scoped = (TourEvent.workspace_id == workspace_id, TourEvent.tour_id == tour_id)
    total = (
        await session.execute(select(func.count()).select_from(TourEvent).where(*scoped))
    ).scalar_one()
    rows = await session.execute(
        select(TourEvent)
        .where(*scoped)
        .order_by(TourEvent.created_at.desc(), TourEvent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows.scalars()), total


# ---------------------------------------------------------------------------
# widget delivery + telemetry
# ---------------------------------------------------------------------------


def widget_tour_out(tour: Tour) -> WidgetTourOut:
    """Public projection — steps (incl. target) + kind/settings/frequency type,
    never audience/schedule internals."""
    frequency = tour.frequency or {}
    return WidgetTourOut(
        id=tour.id,
        name=tour.name,
        kind=tour.kind or "flow",
        steps=[TourStepOut.model_validate(s) for s in tour.steps or []],
        theme=TourTheme.model_validate(tour.theme or {}),
        version=tour.version,
        settings=TourSettings.model_validate(tour.settings or {}),
        frequency_type=str(frequency.get("type") or "until_dismissed"),
    )


def _parse_when(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None  # malformed schedule edge — treat as unset rather than 500
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _in_schedule(schedule: dict[str, Any], now: datetime) -> bool:
    start = _parse_when(schedule.get("start_at"))
    end = _parse_when(schedule.get("end_at"))
    if start is not None and now < start:
        return False
    return not (end is not None and now > end)


def _frequency_allows(
    frequency: dict[str, Any], history: dict[str, datetime], now: datetime
) -> bool:
    """Per-contact re-delivery rules; `history` maps event name → latest occurrence."""
    ftype = frequency.get("type") or "until_dismissed"
    if ftype == "once":
        return "started" not in history
    if ftype == "until_completed":
        return "completed" not in history
    if ftype == "every_time":
        cooldown = frequency.get("cooldown_hours")
        if cooldown and history:
            latest = max(history.values())
            if now - latest < timedelta(hours=float(cooldown)):
                return False
        return True
    # until_dismissed — the default
    return not any(event in history for event in _FINISHED_EVENTS)


async def _event_history(
    session: AsyncSession, workspace_id: str, contact_id: str, tour_ids: list[str]
) -> dict[str, dict[str, datetime]]:
    """One grouped query for all candidate tours: {tour_id: {event: latest_at}}."""
    if not tour_ids:
        return {}
    rows = await session.execute(
        select(TourEvent.tour_id, TourEvent.event, func.max(TourEvent.created_at))
        .where(
            TourEvent.workspace_id == workspace_id,
            TourEvent.contact_id == contact_id,
            TourEvent.tour_id.in_(tour_ids),
        )
        .group_by(TourEvent.tour_id, TourEvent.event)
    )
    history: dict[str, dict[str, datetime]] = {}
    for tour_id, event, latest in rows.all():
        if latest is None:
            continue
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        history.setdefault(tour_id, {})[event] = latest
    return history


async def _audience_matches(
    session: AsyncSession, workspace_id: str, audience: dict[str, Any], contact: Contact | None
) -> bool:
    """ "all" always matches; "filters" audiences require a known contact and are
    evaluated against that single contact (never a workspace scan)."""
    if not audience or audience.get("type") != "filters":
        return True
    filters = audience.get("filters") or []
    if not filters:
        return True
    if contact is None:
        return False
    return await segments_service.contact_matches(session, workspace_id, contact, filters)


async def deliverable_tours(
    session: AsyncSession,
    workspace_id: str,
    *,
    url: str,
    contact: Contact | None,
) -> list[Tour]:
    """Live, URL-matched tours for a page: schedule window + per-contact frequency
    + audience enforced, ordered priority desc / created asc, capped at 5.
    Manual tours are never auto-delivered (see deliverable_tour_by_id).
    Anonymous visitors get no server-side frequency exclusion — the widget's
    local seen-set guards (except `every_time`, which bypasses it there too)."""
    now = utcnow()
    result = await session.execute(
        select(Tour)
        .where(Tour.workspace_id == workspace_id, Tour.status == "live")
        .order_by(Tour.priority.desc(), Tour.created_at.asc(), Tour.id.asc())
    )
    candidates: list[Tour] = []
    for tour in result.scalars():
        trigger = tour.trigger or {}
        if trigger.get("type") != "url_match":
            continue
        pattern = trigger.get("url_pattern")
        if not pattern or not fnmatch(url, pattern):
            continue
        if not _in_schedule(tour.schedule or {}, now):
            continue
        candidates.append(tour)
    if not candidates:
        return []

    history = (
        await _event_history(session, workspace_id, contact.id, [t.id for t in candidates])
        if contact is not None
        else {}
    )

    out: list[Tour] = []
    for tour in candidates:
        if contact is not None and not _frequency_allows(
            tour.frequency or {}, history.get(tour.id, {}), now
        ):
            continue
        if not await _audience_matches(session, workspace_id, tour.audience or {}, contact):
            continue
        out.append(tour)
        if len(out) >= MAX_DELIVERED:
            break
    return out


async def deliverable_tour_by_id(
    session: AsyncSession,
    workspace_id: str,
    tour_id: str,
    *,
    contact: Contact | None,
) -> Tour:
    """Manual start: a single live tour of ANY trigger type.

    Audience still applies — it is a targeting rule, and a tour aimed at
    enterprise trials should not play for everyone else just because something
    asked for it by id.

    Frequency deliberately does NOT apply. It governs *unsolicited* delivery
    ("stop auto-showing this to someone who already finished it"), and this path
    is only reached when the tour was explicitly asked for: `stept('startTour',
    id)` from the host app, or the agent's `show_guide` after the visitor asked
    to be shown. Re-applying it here meant "show me the on-call tour again"
    answered 404 and the widget silently played nothing.
    """
    tour = await get_tour(session, workspace_id, tour_id)
    if tour.status != "live":
        raise NotFoundError("Tour not found")
    if not await _audience_matches(session, workspace_id, tour.audience or {}, contact):
        raise NotFoundError("Tour not found")
    return tour


async def preview_tour(session: AsyncSession, token: str, tour_id: str) -> Tour:
    """Resolve a tour from a preview token, regardless of status/trigger/frequency.
    The token is scoped to exactly one tour id."""
    payload = decode_token(token, "tour_preview")  # 401 on bad/expired/wrong type
    workspace_id = payload.get("ws")
    if not workspace_id or payload.get("tour") != tour_id:
        raise NotFoundError("Tour not found")
    return await get_tour(session, workspace_id, tour_id)


async def record_event(
    session: AsyncSession,
    workspace_id: str,
    tour_id: str,
    *,
    event: str,
    step_index: int | None = None,
    contact_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> TourEvent | None:
    """Record one playback telemetry event (the widget's lifecycle channel).

    Tolerant by contract: an event type outside `KNOWN_TOUR_EVENTS` is accepted
    and ignored (returns None) — never a 422, never a junk row. Lifecycle
    events of agent-initiated tours additionally feed the conversation that
    started them (`app.agents.tour_events`): resuming the parked run, mirroring
    into the transcript, congrats on completion. That glue must never be able
    to fail the telemetry insert itself.
    """
    tour = await get_tour(session, workspace_id, tour_id)
    if event not in KNOWN_TOUR_EVENTS:
        return None
    row = TourEvent(
        workspace_id=workspace_id,
        tour_id=tour.id,
        contact_id=contact_id,
        event=event,
        step_index=step_index,
        meta=meta or {},
    )
    session.add(row)
    await session.flush()
    payload = {
        "id": row.id,
        "tour_id": tour.id,
        "event": event,
        "step_index": step_index,
        "contact_id": contact_id,
        "meta": row.meta,
        "created_at": row.created_at.isoformat(),
    }
    await emit(
        session,
        Event(
            name=EventNames.TOUR_EVENT,
            workspace_id=workspace_id,
            payload=payload,
            actor=Actor(type="contact", id=contact_id) if contact_id else Actor.system(),
        ),
    )
    # Live-update dashboards (analytics event feed) — same pattern as conversations.
    await broadcast(workspace_topic(workspace_id), EventNames.TOUR_EVENT, payload)
    if event == "completed" and contact_id is not None:
        # Soft seam: checklists auto-complete "take the tour" items when present.
        try:
            from app.services import checklists

            await checklists.mark_tour_completed(session, workspace_id, contact_id, tour_id)
        except (ImportError, AttributeError):
            pass
    # Agent-conversation seam: lifecycle truth for tours the AI started.
    try:
        from app.agents import tour_events

        await tour_events.process_event(
            session,
            workspace_id,
            tour,
            event=event,
            contact_id=contact_id,
            step_index=step_index,
            meta=meta,
        )
    except Exception:  # noqa: BLE001 — telemetry must record even if the seam breaks
        import logging

        logging.getLogger("stept.tours").exception(
            "tour lifecycle seam failed for tour %s event %s", tour.id, event
        )
    return row


# ---------------------------------------------------------------------------
# playback health
# ---------------------------------------------------------------------------


async def tour_health(session: AsyncSession, workspace_id: str, tour: Tour) -> dict[str, Any]:
    """Playback-health rollup for one tour, from breakage telemetry.

    - "red":    steps failed to play (`step_error` — anchor missing at play
                time, action failed);
    - "yellow": no hard failures, but visitors got STUCK (`step_blocked` — they
                pressed Next and the next step's anchor was not on the page) or
                self-healing had to recover steps via fallback selectors;
    - "green":  clean.

    `step_blocked` is deliberately at-least-yellow: a tour people cannot finish
    is broken for them even when every step that *did* render resolved fine.
    """
    scoped = (TourEvent.workspace_id == workspace_id, TourEvent.tour_id == tour.id)
    count_rows = await session.execute(
        select(TourEvent.event, TourEvent.step_index, func.count())
        .where(*scoped, TourEvent.event.in_(("step_error", "step_blocked")))
        .group_by(TourEvent.event, TourEvent.step_index)
    )
    errors: dict[int | None, int] = {}
    blocked: dict[int | None, int] = {}
    for event, index, count in count_rows.all():
        bucket = errors if event == "step_error" else blocked
        bucket[index] = bucket.get(index, 0) + count
    healed = (
        await session.execute(
            select(func.count()).where(
                *scoped,
                TourEvent.event == "step_viewed",
                TourEvent.meta["healed"].as_boolean().is_(True),
            )
        )
    ).scalar_one()

    step_titles = [str(step.get("title") or "") for step in tour.steps or []]

    def _steps_out(bucket: dict[int | None, int]) -> list[dict[str, Any]]:
        return [
            {
                "index": index,
                "title": (
                    step_titles[index]
                    if index is not None and 0 <= index < len(step_titles)
                    else ""
                ),
                "count": count,
            }
            for index, count in sorted(bucket.items(), key=lambda i: (i[0] is None, i[0]))
        ]

    if errors:
        health = "red"
    elif blocked or healed:
        health = "yellow"
    else:
        health = "green"
    return {
        "tour_id": tour.id,
        "name": tour.name,
        "status": tour.status,
        "health": health,
        "step_errors": sum(errors.values()),
        "step_blocked": sum(blocked.values()),
        "broken_steps": _steps_out(errors),
        "blocked_steps": _steps_out(blocked),
        "healed_step_views": int(healed or 0),
    }


# ---------------------------------------------------------------------------
# recorder / extension auth
# ---------------------------------------------------------------------------


def mint_recorder_token(workspace_id: str, user_id: str) -> str:
    return create_recorder_token(workspace_id, user_id)


async def _require_tours_manage(session: AsyncSession, workspace_id: str, user_id: str) -> None:
    """The subject must still be a member with tours:manage (re-checked per call)."""
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise ForbiddenError("No longer a member of this workspace")
    custom_perms = (
        list(membership.custom_role.permissions)
        if membership.role == "custom" and membership.custom_role is not None
        else None
    )
    if Perm.TOURS_MANAGE not in resolve_permissions(membership.role, custom_perms):
        raise ForbiddenError("Requires permission tours:manage")


async def authorize_recorder(session: AsyncSession, token: str) -> tuple[str, str]:
    """Validate a recorder token → (workspace_id, user_id).

    Raises UnauthorizedError (401) for a bad/expired/wrong-type token and
    ForbiddenError (403) when the member is gone or lacks the permission."""
    payload = decode_token(token, "recorder")  # 401 on bad/expired/wrong type
    workspace_id = payload.get("ws")
    user_id = payload.get("sub")
    if not workspace_id or not user_id:
        raise UnauthorizedError("Malformed recorder token")
    await _require_tours_manage(session, workspace_id, user_id)
    return workspace_id, user_id


async def authorize_extension(session: AsyncSession, token: str) -> tuple[str, str]:
    """Validate an extension token (recorder tokens accepted for back-compat)
    → (workspace_id, user_id). Same 401/403 semantics as authorize_recorder."""
    try:
        payload = decode_token(token, "extension")
    except UnauthorizedError:
        payload = decode_token(token, "recorder")
    workspace_id = payload.get("ws")
    user_id = payload.get("sub")
    if not workspace_id or not user_id:
        raise UnauthorizedError("Malformed extension token")
    await _require_tours_manage(session, workspace_id, user_id)
    return workspace_id, user_id
