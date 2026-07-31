"""Service layer for slas.

Chatwoot-style SLA semantics (docs/research/chatwoot-gaps.md §3):

- Policies carry frt/nrt/rt thresholds in wall-clock minutes. Business-hours
  awareness is deliberately NOT in v1 — every deadline is computed on the wall
  clock.
- One `AppliedSla` per conversation (applied manually via the API or
  auto-applied from `inbox.config["sla_policy_id"]` on conversation.created).
- The `sla_scan` scheduler job records breach `SlaEvent`s: frt/rt fire once per
  application, nrt once per waiting episode (keyed by `waiting_since` in the
  event meta). Each breach flips the application to "active_with_misses",
  emits `sla.breached`, notifies the assignee, and broadcasts the conversation.
- Resolving finalizes: "active" → "hit", "active_with_misses" → "missed".
  Reopening returns to "active_with_misses" if breaches were recorded, else
  "active".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope, utcnow
from app.core.errors import NotFoundError, ValidationFailure
from app.core.events import Actor, Event, EventNames, emit, on
from app.core.scheduler import scheduled
from app.models.conversation import Conversation, ConversationStatus
from app.models.inbox import Inbox
from app.models.sla import AppliedSla, SlaEvent, SlaEventType, SlaPolicy, SlaStatus
from app.realtime.manager import broadcast, conversation_topic, workspace_topic
from app.services import audit, notifications
from app.services import conversations as conversations_service

_OPEN_APPLIED_STATUSES = (SlaStatus.ACTIVE.value, SlaStatus.ACTIVE_WITH_MISSES.value)
_THRESHOLD_FIELDS = ("first_response_minutes", "next_response_minutes", "resolution_minutes")


def _validate_thresholds(policy: SlaPolicy) -> None:
    if all(getattr(policy, field) is None for field in _THRESHOLD_FIELDS):
        raise ValidationFailure(
            "At least one SLA threshold is required "
            "(first_response_minutes, next_response_minutes or resolution_minutes)"
        )


# ---------------------------------------------------------------------------
# policy CRUD
# ---------------------------------------------------------------------------


async def get_policy(session: AsyncSession, workspace_id: str, policy_id: str) -> SlaPolicy:
    policy = await session.get(SlaPolicy, policy_id)
    if policy is None or policy.workspace_id != workspace_id:
        raise NotFoundError("SLA policy not found")
    return policy


async def list_policies(session: AsyncSession, workspace_id: str) -> list[SlaPolicy]:
    result = await session.execute(
        select(SlaPolicy).where(SlaPolicy.workspace_id == workspace_id).order_by(SlaPolicy.name)
    )
    return list(result.scalars())


async def create_policy(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    name: str,
    description: str | None = None,
    first_response_minutes: int | None = None,
    next_response_minutes: int | None = None,
    resolution_minutes: int | None = None,
) -> SlaPolicy:
    policy = SlaPolicy(
        workspace_id=workspace_id,
        name=name.strip(),
        description=description,
        first_response_minutes=first_response_minutes,
        next_response_minutes=next_response_minutes,
        resolution_minutes=resolution_minutes,
    )
    _validate_thresholds(policy)
    session.add(policy)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="sla_policy.create",
        target_type="sla_policy",
        target_id=policy.id,
        meta={"name": policy.name},
    )
    return policy


async def update_policy(
    session: AsyncSession,
    workspace_id: str,
    policy_id: str,
    *,
    actor: Actor,
    changes: dict[str, Any],
) -> SlaPolicy:
    """Apply the provided fields (from `model_dump(exclude_unset=True)`);
    explicit nulls clear thresholds, subject to the ≥1-threshold invariant."""
    policy = await get_policy(session, workspace_id, policy_id)
    for field in ("name", "description", *_THRESHOLD_FIELDS):
        if field in changes:
            value = changes[field]
            setattr(policy, field, value.strip() if field == "name" and value else value)
    _validate_thresholds(policy)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="sla_policy.update",
        target_type="sla_policy",
        target_id=policy.id,
        meta={"name": policy.name},
    )
    return policy


async def delete_policy(
    session: AsyncSession, workspace_id: str, policy_id: str, *, actor: Actor
) -> None:
    policy = await get_policy(session, workspace_id, policy_id)
    name = policy.name
    await session.delete(policy)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="sla_policy.delete",
        target_type="sla_policy",
        target_id=policy_id,
        meta={"name": name},
    )


# ---------------------------------------------------------------------------
# per-conversation application
# ---------------------------------------------------------------------------


async def get_applied(
    session: AsyncSession, workspace_id: str, conversation_id: str
) -> AppliedSla | None:
    return (
        await session.execute(
            select(AppliedSla).where(
                AppliedSla.workspace_id == workspace_id,
                AppliedSla.conversation_id == conversation_id,
            )
        )
    ).scalar_one_or_none()


async def list_events(session: AsyncSession, applied_sla_id: str) -> list[SlaEvent]:
    result = await session.execute(
        select(SlaEvent)
        .where(SlaEvent.applied_sla_id == applied_sla_id)
        .order_by(SlaEvent.created_at, SlaEvent.id)
    )
    return list(result.scalars())


async def apply_sla(
    session: AsyncSession,
    workspace_id: str,
    conversation: Conversation,
    sla_policy_id: str,
    *,
    actor: Actor,
) -> AppliedSla:
    """Upsert the conversation's SLA application. Re-applying a different policy
    replaces the row in place (breach history of the old policy is dropped so
    once-per-application guards evaluate the new deadlines fresh)."""
    policy = await get_policy(session, workspace_id, sla_policy_id)
    applied = await get_applied(session, workspace_id, conversation.id)
    if applied is None:
        applied = AppliedSla(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            sla_policy_id=policy.id,
            status=SlaStatus.ACTIVE,
        )
        session.add(applied)
    else:
        if applied.sla_policy_id != policy.id:
            await session.execute(delete(SlaEvent).where(SlaEvent.applied_sla_id == applied.id))
            applied.sla_policy_id = policy.id
        applied.status = SlaStatus.ACTIVE
        applied.completed_at = None
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="sla.apply",
        target_type="conversation",
        target_id=conversation.id,
        meta={"sla_policy_id": policy.id, "name": policy.name},
    )
    return applied


async def remove_sla(
    session: AsyncSession, workspace_id: str, conversation: Conversation, *, actor: Actor
) -> None:
    applied = await get_applied(session, workspace_id, conversation.id)
    if applied is None:
        return
    # Explicit event cleanup — portable regardless of FK enforcement (SQLite).
    await session.execute(delete(SlaEvent).where(SlaEvent.applied_sla_id == applied.id))
    await session.delete(applied)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="sla.remove",
        target_type="conversation",
        target_id=conversation.id,
        meta={"sla_policy_id": applied.sla_policy_id},
    )


# ---------------------------------------------------------------------------
# event handlers: auto-apply + finalize
# ---------------------------------------------------------------------------


@on(EventNames.CONVERSATION_CREATED)
async def _auto_apply_on_conversation_created(session: AsyncSession, event: Event) -> None:
    conversation_id = event.payload.get("conversation_id")
    if not conversation_id:
        return
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.workspace_id != event.workspace_id:
        return
    inbox = await session.get(Inbox, conversation.inbox_id)
    if inbox is None:
        return
    policy_id = (inbox.config or {}).get("sla_policy_id")
    if not policy_id:
        return
    policy = await session.get(SlaPolicy, str(policy_id))
    if policy is None or policy.workspace_id != conversation.workspace_id:
        return  # stale/cross-workspace config — never apply
    await apply_sla(
        session, conversation.workspace_id, conversation, policy.id, actor=Actor.system()
    )


@on(EventNames.CONVERSATION_STATUS_CHANGED)
async def _finalize_on_status_change(session: AsyncSession, event: Event) -> None:
    conversation_id = event.payload.get("conversation_id")
    status = event.payload.get("status")
    previous = event.payload.get("previous_status")
    if not conversation_id:
        return
    applied = await get_applied(session, event.workspace_id, str(conversation_id))
    if applied is None:
        return
    if status == ConversationStatus.RESOLVED.value and applied.status in _OPEN_APPLIED_STATUSES:
        applied.status = (
            SlaStatus.HIT if applied.status == SlaStatus.ACTIVE.value else SlaStatus.MISSED
        )
        applied.completed_at = utcnow()
        await session.flush()
    elif (
        previous == ConversationStatus.RESOLVED.value
        and status in (ConversationStatus.OPEN.value, ConversationStatus.PENDING.value)
        and applied.status in (SlaStatus.HIT.value, SlaStatus.MISSED.value)
    ):
        breaches = (
            await session.execute(
                select(func.count())
                .select_from(SlaEvent)
                .where(SlaEvent.applied_sla_id == applied.id)
            )
        ).scalar_one()
        applied.status = SlaStatus.ACTIVE_WITH_MISSES if breaches else SlaStatus.ACTIVE
        applied.completed_at = None
        await session.flush()


# ---------------------------------------------------------------------------
# breach scan (scheduler job)
# ---------------------------------------------------------------------------


async def scan_sla_breaches(now: datetime | None = None) -> int:
    """Evaluate every open SLA application; returns the number of new breach
    events recorded. Runs in its own unit of work (scheduler entry point)."""
    now = now or utcnow()
    recorded = 0
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(AppliedSla, SlaPolicy, Conversation)
                .join(SlaPolicy, SlaPolicy.id == AppliedSla.sla_policy_id)
                .join(Conversation, Conversation.id == AppliedSla.conversation_id)
                .where(AppliedSla.status.in_(_OPEN_APPLIED_STATUSES))
                .order_by(AppliedSla.created_at, AppliedSla.id)
            )
        ).all()
        for applied, policy, conversation in rows:
            recorded += await _evaluate_applied_sla(session, applied, policy, conversation, now)
    return recorded


@scheduled("sla_scan", every_seconds=60)
async def _sla_scan_job() -> None:
    await scan_sla_breaches()


async def _evaluate_applied_sla(
    session: AsyncSession,
    applied: AppliedSla,
    policy: SlaPolicy,
    conversation: Conversation,
    now: datetime,
) -> int:
    events = list(
        (
            await session.execute(select(SlaEvent).where(SlaEvent.applied_sla_id == applied.id))
        ).scalars()
    )
    seen_types = {event.event_type for event in events}
    seen_nrt_episodes = {
        (event.meta or {}).get("waiting_since")
        for event in events
        if event.event_type == SlaEventType.NRT.value
    }
    resolved = conversation.status == ConversationStatus.RESOLVED.value
    recorded = 0

    # frt — no first reply yet; once per application; response targets stop at resolve.
    if (
        policy.first_response_minutes is not None
        and not resolved
        and conversation.first_reply_at is None
        and SlaEventType.FRT.value not in seen_types
        and now > conversation.created_at + timedelta(minutes=policy.first_response_minutes)
    ):
        await _record_breach(session, applied, conversation, SlaEventType.FRT.value, {})
        recorded += 1

    # nrt — after the first reply, once per waiting episode (keyed by waiting_since).
    if (
        policy.next_response_minutes is not None
        and not resolved
        and conversation.first_reply_at is not None
        and conversation.waiting_since is not None
        and now > conversation.waiting_since + timedelta(minutes=policy.next_response_minutes)
    ):
        episode = conversation.waiting_since.isoformat()
        if episode not in seen_nrt_episodes:
            await _record_breach(
                session,
                applied,
                conversation,
                SlaEventType.NRT.value,
                {"waiting_since": episode},
            )
            recorded += 1

    # rt — not resolved in time; once per application.
    if (
        policy.resolution_minutes is not None
        and conversation.resolved_at is None
        and SlaEventType.RT.value not in seen_types
        and now > conversation.created_at + timedelta(minutes=policy.resolution_minutes)
    ):
        await _record_breach(session, applied, conversation, SlaEventType.RT.value, {})
        recorded += 1
    return recorded


async def _record_breach(
    session: AsyncSession,
    applied: AppliedSla,
    conversation: Conversation,
    event_type: str,
    meta: dict[str, Any],
) -> None:
    session.add(
        SlaEvent(
            workspace_id=applied.workspace_id,
            applied_sla_id=applied.id,
            conversation_id=conversation.id,
            event_type=event_type,
            meta=meta,
        )
    )
    applied.status = SlaStatus.ACTIVE_WITH_MISSES
    await session.flush()
    await emit(
        session,
        Event(
            name=EventNames.SLA_BREACHED,
            workspace_id=applied.workspace_id,
            payload={
                "conversation_id": conversation.id,
                "sla_policy_id": applied.sla_policy_id,
                "event_type": event_type,
            },
        ),
    )
    if conversation.assignee_user_id is not None:
        await notifications.notify(
            session,
            applied.workspace_id,
            conversation.assignee_user_id,
            type="sla",
            title=f"SLA {event_type.upper()} missed",
            link=f"/inbox/{conversation.id}",
        )
    # Same broadcast shape as the conversations service ("conversation.updated"
    # with the full detail payload, on both the workspace and conversation topics).
    payload = (await conversations_service.conversation_out(session, conversation)).model_dump(
        mode="json"
    )
    await broadcast(workspace_topic(conversation.workspace_id), "conversation.updated", payload)
    await broadcast(conversation_topic(conversation.id), "conversation.updated", payload)
