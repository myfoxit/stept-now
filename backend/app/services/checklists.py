"""Checklist service: authoring CRUD, publish/pause, per-contact progress,
widget delivery, stats, and the tour-completion seam.

Delivery mirrors tour delivery: live status, fnmatch URL trigger, audience via
`segments.contact_matches`, ordered by priority. A checklist the contact already
completed or dismissed is never delivered again.

`mark_tour_completed` is the seam the tours service calls (soft import) when a
tour is completed, so `completion.type == "tour_completed"` items check
themselves off server-side for identified contacts.
"""

from __future__ import annotations

import json
from fnmatch import fnmatch
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow, uuid7
from app.core.errors import ConflictError, NotFoundError, ValidationFailure
from app.core.events import Actor, Event, EventNames, emit
from app.core.pubsub import get_pubsub
from app.models.checklist import Checklist, ChecklistProgress
from app.models.contact import Contact
from app.schemas.checklists import (
    MAX_ITEMS,
    ChecklistItemStat,
    ChecklistStats,
    WidgetChecklistOut,
    WidgetChecklistProgress,
)
from app.services import audit
from app.services import segments as segments_service

DEFAULT_ACCENT = "#6366f1"
DELIVERY_LIMIT = 5


# ---------------------------------------------------------------------------
# normalization helpers
# ---------------------------------------------------------------------------


def _normalize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every item a stable id and the full field set."""
    if len(items) > MAX_ITEMS:
        raise ValidationFailure(f"A checklist can hold at most {MAX_ITEMS} items")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        item_id = str(item.get("id") or uuid7())
        if item_id in seen:
            raise ValidationFailure("Checklist item ids must be unique")
        seen.add(item_id)
        action = dict(item.get("action") or {})
        completion = dict(item.get("completion") or {})
        out.append(
            {
                "id": item_id,
                "title": (item.get("title") or "").strip(),
                "body": item.get("body") or "",
                "action": {
                    "type": action.get("type") or "none",
                    "tour_id": action.get("tour_id"),
                    "url": action.get("url"),
                },
                "completion": {
                    "type": completion.get("type") or "manual",
                    "tour_id": completion.get("tour_id"),
                    "url_pattern": completion.get("url_pattern"),
                },
            }
        )
    return out


def _items_signature(items: list[dict[str, Any]]) -> list[str]:
    """Content fingerprint (ignores ids) used to decide if a PATCH changed items."""
    return [
        json.dumps(
            [item.get("title"), item.get("body"), item.get("action"), item.get("completion")],
            sort_keys=True,
            default=str,
        )
        for item in items
    ]


async def _broadcast(workspace_id: str, payload: dict[str, Any]) -> None:
    """Realtime fan-out so dashboards live-update (mirrors tour telemetry)."""
    await get_pubsub().publish(
        f"ws:{workspace_id}", {"type": EventNames.CHECKLIST_EVENT, "data": payload}
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def get_checklist(session: AsyncSession, workspace_id: str, checklist_id: str) -> Checklist:
    checklist = await session.get(Checklist, checklist_id)
    if checklist is None or checklist.workspace_id != workspace_id:
        raise NotFoundError("Checklist not found")
    return checklist


async def list_checklists(session: AsyncSession, workspace_id: str) -> list[Checklist]:
    result = await session.execute(
        select(Checklist)
        .where(Checklist.workspace_id == workspace_id)
        .order_by(Checklist.created_at.desc(), Checklist.id.desc())
    )
    return list(result.scalars())


async def create_checklist(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    name: str,
    description: str = "",
    items: list[dict[str, Any]] | None = None,
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    theme: dict[str, Any] | None = None,
    launcher: dict[str, Any] | None = None,
    priority: int = 0,
) -> Checklist:
    checklist = Checklist(
        workspace_id=workspace_id,
        name=name.strip(),
        description=description or "",
        status="draft",
        items=_normalize_items(items or []),
        trigger=trigger or {"type": "url_match", "url_pattern": "*"},
        audience=audience or {"type": "all"},
        theme=theme or {"accent": DEFAULT_ACCENT, "position": "bottom-right"},
        launcher=launcher or {"label": "Getting started", "auto_open_once": True},
        priority=priority,
        version=1,
        created_by=actor.id if actor.type == "user" else None,
    )
    session.add(checklist)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="checklist.create",
        target_type="checklist",
        target_id=checklist.id,
        meta={"name": checklist.name},
    )
    return checklist


async def update_checklist(
    session: AsyncSession,
    workspace_id: str,
    checklist_id: str,
    *,
    actor: Actor,
    name: str | None = None,
    description: str | None = None,
    items: list[dict[str, Any]] | None = None,
    trigger: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
    theme: dict[str, Any] | None = None,
    launcher: dict[str, Any] | None = None,
    priority: int | None = None,
) -> Checklist:
    checklist = await get_checklist(session, workspace_id, checklist_id)
    if name is not None:
        checklist.name = name.strip()
    if description is not None:
        checklist.description = description
    if trigger is not None:
        checklist.trigger = trigger
    if audience is not None:
        checklist.audience = audience
    if theme is not None:
        checklist.theme = theme
    if launcher is not None:
        checklist.launcher = launcher
    if priority is not None:
        checklist.priority = priority
    if items is not None:
        normalized = _normalize_items(items)
        # Only bump the version when item content actually changed.
        if _items_signature(normalized) != _items_signature(checklist.items):
            checklist.items = normalized
            checklist.version += 1
        else:
            checklist.items = normalized  # ids may have been (re)assigned
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="checklist.update",
        target_type="checklist",
        target_id=checklist.id,
        meta={"name": checklist.name, "version": checklist.version},
    )
    return checklist


async def delete_checklist(
    session: AsyncSession, workspace_id: str, checklist_id: str, *, actor: Actor
) -> None:
    checklist = await get_checklist(session, workspace_id, checklist_id)
    name = checklist.name
    await session.delete(checklist)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="checklist.delete",
        target_type="checklist",
        target_id=checklist_id,
        meta={"name": name},
    )


async def set_status(
    session: AsyncSession, workspace_id: str, checklist_id: str, *, actor: Actor, status: str
) -> Checklist:
    checklist = await get_checklist(session, workspace_id, checklist_id)
    if status == "live" and not checklist.items:
        raise ConflictError("Add at least one item before publishing")
    checklist.status = status
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action=f"checklist.{'publish' if status == 'live' else status}",
        target_type="checklist",
        target_id=checklist.id,
        meta={"status": status},
    )
    return checklist


async def publish_checklist(
    session: AsyncSession, workspace_id: str, checklist_id: str, *, actor: Actor
) -> Checklist:
    return await set_status(session, workspace_id, checklist_id, actor=actor, status="live")


async def pause_checklist(
    session: AsyncSession, workspace_id: str, checklist_id: str, *, actor: Actor
) -> Checklist:
    return await set_status(session, workspace_id, checklist_id, actor=actor, status="paused")


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------


async def get_progress(
    session: AsyncSession, workspace_id: str, checklist_id: str, contact_id: str
) -> ChecklistProgress | None:
    return (
        await session.execute(
            select(ChecklistProgress).where(
                ChecklistProgress.workspace_id == workspace_id,
                ChecklistProgress.checklist_id == checklist_id,
                ChecklistProgress.contact_id == contact_id,
            )
        )
    ).scalar_one_or_none()


async def _get_or_create_progress(
    session: AsyncSession, workspace_id: str, checklist_id: str, contact_id: str
) -> ChecklistProgress:
    progress = await get_progress(session, workspace_id, checklist_id, contact_id)
    if progress is None:
        progress = ChecklistProgress(
            workspace_id=workspace_id,
            checklist_id=checklist_id,
            contact_id=contact_id,
            item_state={},
        )
        session.add(progress)
        await session.flush()
    return progress


def _sync_completion(checklist: Checklist, progress: ChecklistProgress) -> bool:
    """Set/clear completed_at from the item state. Returns True when complete."""
    item_ids = [item["id"] for item in checklist.items]
    complete = bool(item_ids) and all(item_id in progress.item_state for item_id in item_ids)
    if complete and progress.completed_at is None:
        progress.completed_at = utcnow()
    elif not complete:
        progress.completed_at = None
    return complete


async def _apply_items(
    session: AsyncSession,
    workspace_id: str,
    checklist: Checklist,
    contact_id: str,
    *,
    item_ids: list[str],
    done: bool,
) -> ChecklistProgress:
    """Set/unset several items at once, emitting one event per item."""
    progress = await _get_or_create_progress(session, workspace_id, checklist.id, contact_id)
    # JSON columns need reassignment for SQLAlchemy to see the change.
    state = dict(progress.item_state or {})
    stamp = utcnow().isoformat()
    changed: list[str] = []
    for item_id in item_ids:
        if done:
            if item_id not in state:
                state[item_id] = stamp
                changed.append(item_id)
        elif item_id in state:
            state.pop(item_id)
            changed.append(item_id)
    progress.item_state = state
    completed = _sync_completion(checklist, progress)
    await session.flush()

    for item_id in changed:
        payload = {
            "checklist_id": checklist.id,
            "item_id": item_id,
            "done": done,
            "contact_id": contact_id,
            "completed": completed,
        }
        await emit(
            session,
            Event(
                name=EventNames.CHECKLIST_EVENT,
                workspace_id=workspace_id,
                payload=payload,
                actor=Actor(type="contact", id=contact_id),
            ),
        )
        await _broadcast(workspace_id, payload)
    return progress


async def record_checklist_progress(
    session: AsyncSession,
    workspace_id: str,
    checklist: Checklist,
    contact_id: str,
    *,
    item_id: str,
    done: bool,
) -> ChecklistProgress:
    """Check (or uncheck) one item for an identified contact."""
    if item_id not in {item["id"] for item in checklist.items}:
        raise ValidationFailure("Unknown checklist item")
    return await _apply_items(
        session, workspace_id, checklist, contact_id, item_ids=[item_id], done=done
    )


async def dismiss_checklist(
    session: AsyncSession, workspace_id: str, checklist: Checklist, contact_id: str
) -> ChecklistProgress:
    progress = await _get_or_create_progress(session, workspace_id, checklist.id, contact_id)
    progress.dismissed_at = utcnow()
    await session.flush()
    payload = {
        "checklist_id": checklist.id,
        "item_id": None,
        "done": False,
        "contact_id": contact_id,
        "completed": progress.completed_at is not None,
        "dismissed": True,
    }
    await emit(
        session,
        Event(
            name=EventNames.CHECKLIST_EVENT,
            workspace_id=workspace_id,
            payload=payload,
            actor=Actor(type="contact", id=contact_id),
        ),
    )
    await _broadcast(workspace_id, payload)
    return progress


async def mark_tour_completed(
    session: AsyncSession, workspace_id: str, contact_id: str | None, tour_id: str
) -> None:
    """Auto-complete `completion.type == "tour_completed"` items for that tour.

    Called by the tours service when a tour is completed (soft import — a missing
    checklists module must never break telemetry). Anonymous visitors are handled
    optimistically client-side, so a missing contact is a no-op.
    """
    if not contact_id or not tour_id:
        return
    rows = await session.execute(
        select(Checklist).where(Checklist.workspace_id == workspace_id, Checklist.status == "live")
    )
    for checklist in rows.scalars():
        item_ids = [
            item["id"]
            for item in checklist.items
            if (item.get("completion") or {}).get("type") == "tour_completed"
            and (item.get("completion") or {}).get("tour_id") == tour_id
        ]
        if item_ids:
            await _apply_items(
                session, workspace_id, checklist, contact_id, item_ids=item_ids, done=True
            )


# ---------------------------------------------------------------------------
# widget delivery
# ---------------------------------------------------------------------------


async def _audience_matches(
    session: AsyncSession, workspace_id: str, audience: dict[str, Any], contact: Contact | None
) -> bool:
    """ "all" always matches; "filters" audiences require a known contact."""
    if not audience or audience.get("type") != "filters":
        return True
    filters = audience.get("filters") or []
    if not filters:
        return True
    if contact is None:
        return False
    return await segments_service.contact_matches(session, workspace_id, contact, filters)


def widget_payload(checklist: Checklist, progress: ChecklistProgress | None) -> dict[str, Any]:
    """Public projection + this contact's progress (empty for anonymous)."""
    return WidgetChecklistOut.model_validate(
        {
            "id": checklist.id,
            "name": checklist.name,
            "description": checklist.description,
            "items": checklist.items,
            "theme": checklist.theme or {},
            "launcher": checklist.launcher or {},
            "version": checklist.version,
            "progress": WidgetChecklistProgress(
                item_state=dict(progress.item_state or {}) if progress else {},
                dismissed=bool(progress and progress.dismissed_at),
                completed=bool(progress and progress.completed_at),
            ),
        }
    ).model_dump(mode="json")


async def deliverable_checklists(
    session: AsyncSession,
    workspace_id: str,
    *,
    url: str,
    contact: Contact | None,
) -> list[dict[str, Any]]:
    """Live, URL-matched checklists for a page, minus ones this contact already
    completed or dismissed. Anonymous visitors get everything they target (the
    widget hides finished ones from local state)."""
    result = await session.execute(
        select(Checklist)
        .where(Checklist.workspace_id == workspace_id, Checklist.status == "live")
        .order_by(Checklist.priority.desc(), Checklist.created_at.asc(), Checklist.id.asc())
    )
    checklists = list(result.scalars())
    if not checklists:
        return []

    progress_by_checklist: dict[str, ChecklistProgress] = {}
    if contact is not None:
        rows = await session.execute(
            select(ChecklistProgress).where(
                ChecklistProgress.workspace_id == workspace_id,
                ChecklistProgress.contact_id == contact.id,
                ChecklistProgress.checklist_id.in_([c.id for c in checklists]),
            )
        )
        progress_by_checklist = {p.checklist_id: p for p in rows.scalars()}

    out: list[dict[str, Any]] = []
    for checklist in checklists:
        trigger = checklist.trigger or {}
        if trigger.get("type") != "url_match":
            continue
        pattern = trigger.get("url_pattern")
        if not pattern or not fnmatch(url, pattern):
            continue
        progress = progress_by_checklist.get(checklist.id)
        if progress is not None and (progress.dismissed_at or progress.completed_at):
            continue
        if not await _audience_matches(session, workspace_id, checklist.audience or {}, contact):
            continue
        out.append(widget_payload(checklist, progress))
        if len(out) >= DELIVERY_LIMIT:
            break
    return out


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


async def compute_stats(
    session: AsyncSession, workspace_id: str, checklist_id: str
) -> ChecklistStats:
    checklist = await get_checklist(session, workspace_id, checklist_id)
    rows = (
        await session.execute(
            select(ChecklistProgress).where(
                ChecklistProgress.workspace_id == workspace_id,
                ChecklistProgress.checklist_id == checklist_id,
            )
        )
    ).scalars()
    progress_rows = list(rows)

    starts = len(progress_rows)
    completions = sum(1 for p in progress_rows if p.completed_at is not None)
    per_item: list[ChecklistItemStat] = []
    for item in checklist.items:
        item_id = item["id"]
        per_item.append(
            ChecklistItemStat(
                id=item_id,
                title=item.get("title", ""),
                completed_count=sum(1 for p in progress_rows if item_id in (p.item_state or {})),
            )
        )
    return ChecklistStats(
        starts=starts,
        completions=completions,
        completion_rate=round(completions / starts, 4) if starts else 0.0,
        items=per_item,
    )
