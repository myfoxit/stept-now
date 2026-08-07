"""Per-inbox working hours: CRUD, schedule loading, and the out-of-office check.

Inbox-level switches live in `inbox.config` (same place as `sla_policy_id`):
    working_hours_enabled: bool
    timezone: str                 # IANA name, default "UTC"
    out_of_office_message: str    # shown by the widget while closed

`schedule_for_inbox` returns `ALWAYS_OPEN` whenever the feature is off or no day
rows exist, so every caller can compute unconditionally and a workspace that
never configures anything behaves exactly as it does today.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_hours import ALWAYS_OPEN, MINUTES_PER_DAY, DayWindow, Schedule, is_open
from app.core.errors import NotFoundError, ValidationFailure
from app.core.events import Actor
from app.models.business_hours import WorkingHour
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.services import audit

# Mon–Fri 09:00–17:00 — the schedule offered when a workspace enables the
# feature without drawing one (matches Chatwoot's auto-seed).
DEFAULT_WEEK: list[dict[str, Any]] = [
    {"day_of_week": d, "closed_all_day": d >= 5, "open_minute": 9 * 60, "close_minute": 17 * 60}
    for d in range(7)
]


def _validate_day(row: dict[str, Any]) -> None:
    day = row.get("day_of_week")
    if not isinstance(day, int) or not 0 <= day <= 6:
        raise ValidationFailure("day_of_week must be an integer 0 (Monday) – 6 (Sunday)")
    if row.get("closed_all_day") or row.get("open_all_day"):
        return
    open_minute, close_minute = row.get("open_minute", 0), row.get("close_minute", 0)
    for name, value in (("open_minute", open_minute), ("close_minute", close_minute)):
        if not isinstance(value, int) or not 0 <= value <= MINUTES_PER_DAY:
            raise ValidationFailure(f"{name} must be an integer between 0 and {MINUTES_PER_DAY}")
    if close_minute <= open_minute:
        raise ValidationFailure("close_minute must be after open_minute")


async def get_inbox(session: AsyncSession, workspace_id: str, inbox_id: str) -> Inbox:
    inbox = await session.get(Inbox, inbox_id)
    if inbox is None or inbox.workspace_id != workspace_id:
        raise NotFoundError("Inbox not found")
    return inbox


async def list_hours(session: AsyncSession, workspace_id: str, inbox_id: str) -> list[WorkingHour]:
    result = await session.execute(
        select(WorkingHour)
        .where(WorkingHour.workspace_id == workspace_id, WorkingHour.inbox_id == inbox_id)
        .order_by(WorkingHour.day_of_week)
    )
    return list(result.scalars())


async def replace_hours(
    session: AsyncSession,
    workspace_id: str,
    inbox_id: str,
    *,
    actor: Actor,
    days: list[dict[str, Any]],
    enabled: bool | None = None,
    timezone: str | None = None,
    out_of_office_message: str | None = None,
) -> list[WorkingHour]:
    """Replace the whole week in one call — a partial weekly schedule is almost
    always a bug, and full replacement keeps the unique (inbox, day) invariant
    trivially true."""
    inbox = await get_inbox(session, workspace_id, inbox_id)
    seen: set[int] = set()
    for row in days:
        _validate_day(row)
        day = int(row["day_of_week"])
        if day in seen:
            raise ValidationFailure(f"Duplicate day_of_week {day}")
        seen.add(day)

    await session.execute(
        delete(WorkingHour).where(
            WorkingHour.workspace_id == workspace_id, WorkingHour.inbox_id == inbox_id
        )
    )
    for row in days:
        session.add(
            WorkingHour(
                workspace_id=workspace_id,
                inbox_id=inbox_id,
                day_of_week=int(row["day_of_week"]),
                closed_all_day=bool(row.get("closed_all_day", False)),
                open_all_day=bool(row.get("open_all_day", False)),
                open_minute=int(row.get("open_minute", 9 * 60)),
                close_minute=int(row.get("close_minute", 17 * 60)),
            )
        )

    config = dict(inbox.config or {})
    if enabled is not None:
        config["working_hours_enabled"] = bool(enabled)
    if timezone is not None:
        config["timezone"] = timezone
    if out_of_office_message is not None:
        config["out_of_office_message"] = out_of_office_message
    inbox.config = config

    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="working_hours.update",
        target_type="inbox",
        target_id=inbox_id,
        meta={"days": len(days), "enabled": config.get("working_hours_enabled", False)},
    )
    return await list_hours(session, workspace_id, inbox_id)


def build_schedule(inbox: Inbox, hours: list[WorkingHour]) -> Schedule:
    config = inbox.config or {}
    if not config.get("working_hours_enabled"):
        return ALWAYS_OPEN
    days: list[DayWindow | None] = [None] * 7
    for row in hours:
        if row.closed_all_day or not 0 <= row.day_of_week <= 6:
            continue
        if row.open_all_day:
            days[row.day_of_week] = DayWindow(0, MINUTES_PER_DAY)
        else:
            days[row.day_of_week] = DayWindow(row.open_minute, row.close_minute)
    if not any(days):
        return ALWAYS_OPEN
    return Schedule(timezone=str(config.get("timezone") or "UTC"), days=tuple(days))


async def schedule_for_inbox(session: AsyncSession, workspace_id: str, inbox_id: str) -> Schedule:
    inbox = await session.get(Inbox, inbox_id)
    if inbox is None or inbox.workspace_id != workspace_id:
        return ALWAYS_OPEN
    return build_schedule(inbox, await list_hours(session, workspace_id, inbox_id))


async def schedule_for_conversation(session: AsyncSession, conversation: Conversation) -> Schedule:
    return await schedule_for_inbox(session, conversation.workspace_id, conversation.inbox_id)


async def inbox_is_open(
    session: AsyncSession, workspace_id: str, inbox_id: str, at: datetime | None = None
) -> bool:
    from app.core.db import utcnow

    schedule = await schedule_for_inbox(session, workspace_id, inbox_id)
    return is_open(schedule, at or utcnow())


def out_of_office_message(inbox: Inbox) -> str | None:
    config = inbox.config or {}
    if not config.get("working_hours_enabled"):
        return None
    message = config.get("out_of_office_message")
    return str(message) if message else None
