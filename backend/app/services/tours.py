"""Tour service: authoring CRUD, publish/pause, funnel stats, widget delivery,
telemetry recording, and the recorder-token → draft-tour flow.

Delivery reuses Agent A's `segments.apply_filters` so audience targeting matches
contact segments exactly; URL triggers use fnmatch-style `*` globbing.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import uuid7
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.events import Actor, Event, EventNames, emit
from app.core.permissions import Perm, resolve_permissions
from app.core.security import create_recorder_token, decode_token
from app.models.contact import Contact
from app.models.tour import Tour, TourEvent
from app.models.workspace import Membership
from app.schemas.tours import TourStats, TourStepStat
from app.services import audit
from app.services import segments as segments_service

_FINISHED_EVENTS = ("completed", "dismissed")
DEFAULT_ACCENT = "#6366f1"


# ---------------------------------------------------------------------------
# normalization helpers
# ---------------------------------------------------------------------------


def _normalize_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every step a stable id and the full field set (app authoring)."""
    out: list[dict[str, Any]] = []
    for step in steps:
        out.append(
            {
                "id": step.get("id") or uuid7(),
                "selector": step["selector"],
                "title": step.get("title") or "",
                "body": step.get("body") or "",
                "placement": step.get("placement") or "auto",
            }
        )
    return out


def _steps_signature(steps: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """Content fingerprint (ignores ids) used to decide if a PATCH changed steps."""
    return [(s["selector"], s["title"], s["body"], s["placement"]) for s in steps]


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
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    theme: dict[str, Any] | None = None,
) -> Tour:
    tour = Tour(
        workspace_id=workspace_id,
        name=name.strip(),
        description=description or "",
        status="draft",
        trigger=trigger or {"type": "manual"},
        audience=audience or {"type": "all"},
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
        meta={"name": tour.name},
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
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    theme: dict[str, Any] | None = None,
) -> Tour:
    tour = await get_tour(session, workspace_id, tour_id)
    if name is not None:
        tour.name = name.strip()
    if description is not None:
        tour.description = description
    if trigger is not None:
        tour.trigger = trigger
    if audience is not None:
        tour.audience = audience
    if theme is not None:
        tour.theme = theme
    if steps is not None:
        normalized = _normalize_steps(steps)
        # Only bump the version when step content actually changed.
        if _steps_signature(normalized) != _steps_signature(tour.steps):
            tour.steps = normalized
            tour.version += 1
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
# stats
# ---------------------------------------------------------------------------


async def compute_stats(session: AsyncSession, workspace_id: str, tour_id: str) -> TourStats:
    tour = await get_tour(session, workspace_id, tour_id)
    rows = (
        await session.execute(
            select(TourEvent).where(
                TourEvent.workspace_id == workspace_id, TourEvent.tour_id == tour_id
            )
        )
    ).scalars()
    events = list(rows)

    starts = sum(1 for e in events if e.event == "started")
    completions = sum(1 for e in events if e.event == "completed")
    dismissals = sum(1 for e in events if e.event == "dismissed")

    step_count = len(tour.steps)
    viewed = [0] * step_count
    for e in events:
        if e.event == "step_viewed" and e.step_index is not None and 0 <= e.step_index < step_count:
            viewed[e.step_index] += 1

    step_stats: list[TourStepStat] = []
    for i, step in enumerate(tour.steps):
        # Drop-off = viewers of this step who did not reach the next step (or, for
        # the final step, who did not complete).
        nxt = viewed[i + 1] if i + 1 < step_count else completions
        step_stats.append(
            TourStepStat(
                index=i,
                title=step.get("title", ""),
                viewed=viewed[i],
                drop_off=max(viewed[i] - nxt, 0),
            )
        )

    return TourStats(
        starts=starts,
        completions=completions,
        dismissals=dismissals,
        completion_rate=round(completions / starts, 4) if starts else 0.0,
        steps=step_stats,
    )


# ---------------------------------------------------------------------------
# widget delivery + telemetry
# ---------------------------------------------------------------------------


async def _finished_tour_ids(session: AsyncSession, workspace_id: str, contact_id: str) -> set[str]:
    rows = await session.execute(
        select(TourEvent.tour_id).where(
            TourEvent.workspace_id == workspace_id,
            TourEvent.contact_id == contact_id,
            TourEvent.event.in_(_FINISHED_EVENTS),
        )
    )
    return set(rows.scalars())


async def _audience_matches(
    session: AsyncSession, workspace_id: str, audience: dict[str, Any], contact: Contact | None
) -> bool:
    """True if the contact is in the tour's audience.

    "all" always matches. "filters" audiences require a known contact and reuse
    Agent A's segment filter engine; anonymous visitors never match a targeted
    tour.
    """
    if not audience or audience.get("type") != "filters":
        return True
    filters = audience.get("filters") or []
    if not filters:
        return True
    if contact is None:
        return False
    matches = await segments_service.apply_filters(session, workspace_id, filters)
    return any(c.id == contact.id for c in matches)


async def deliverable_tours(
    session: AsyncSession,
    workspace_id: str,
    *,
    url: str,
    contact: Contact | None,
) -> list[Tour]:
    """Live, URL-matched tours for a page, minus ones the contact already finished
    and minus ones whose audience excludes the contact. Manual tours are never
    auto-delivered."""
    result = await session.execute(
        select(Tour).where(Tour.workspace_id == workspace_id, Tour.status == "live")
    )
    tours = list(result.scalars())

    finished = (
        await _finished_tour_ids(session, workspace_id, contact.id)
        if contact is not None
        else set()
    )

    out: list[Tour] = []
    for tour in tours:
        trigger = tour.trigger or {}
        if trigger.get("type") != "url_match":
            continue
        pattern = trigger.get("url_pattern")
        if not pattern or not fnmatch(url, pattern):
            continue
        if tour.id in finished:
            continue
        if not await _audience_matches(session, workspace_id, tour.audience or {}, contact):
            continue
        out.append(tour)
    return out


async def record_event(
    session: AsyncSession,
    workspace_id: str,
    tour_id: str,
    *,
    event: str,
    step_index: int | None = None,
    contact_id: str | None = None,
) -> TourEvent:
    tour = await get_tour(session, workspace_id, tour_id)
    row = TourEvent(
        workspace_id=workspace_id,
        tour_id=tour.id,
        contact_id=contact_id,
        event=event,
        step_index=step_index,
    )
    session.add(row)
    await session.flush()
    await emit(
        session,
        Event(
            name=EventNames.TOUR_EVENT,
            workspace_id=workspace_id,
            payload={
                "tour_id": tour.id,
                "event": event,
                "step_index": step_index,
                "contact_id": contact_id,
            },
            actor=Actor(type="contact", id=contact_id) if contact_id else Actor.system(),
        ),
    )
    return row


# ---------------------------------------------------------------------------
# recorder flow
# ---------------------------------------------------------------------------


def mint_recorder_token(workspace_id: str, user_id: str) -> str:
    return create_recorder_token(workspace_id, user_id)


async def authorize_recorder(session: AsyncSession, token: str) -> tuple[str, str]:
    """Validate a recorder token: decode it, confirm the subject is still a member
    with tours:manage. Returns (workspace_id, user_id).

    Raises UnauthorizedError (401) for a bad/expired/wrong-type token and
    ForbiddenError (403) when the member is gone or lacks the permission."""
    payload = decode_token(token, "recorder")  # 401 on bad/expired/wrong type
    workspace_id = payload.get("ws")
    user_id = payload.get("sub")
    if not workspace_id or not user_id:
        raise UnauthorizedError("Malformed recorder token")

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
    return workspace_id, user_id


async def create_tour_from_recorder(
    session: AsyncSession,
    workspace_id: str,
    *,
    user_id: str,
    name: str,
    url_pattern: str | None,
    steps: list[dict[str, Any]],
) -> Tour:
    built: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        built.append(
            {
                "id": uuid7(),
                "selector": step["selector"],
                "title": step.get("title") or f"Step {i + 1}",
                "body": step.get("body") or "",
                "placement": "auto",
            }
        )
    trigger = (
        {"type": "url_match", "url_pattern": url_pattern} if url_pattern else {"type": "manual"}
    )
    tour = Tour(
        workspace_id=workspace_id,
        name=name.strip(),
        description="",
        status="draft",
        trigger=trigger,
        audience={"type": "all"},
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
