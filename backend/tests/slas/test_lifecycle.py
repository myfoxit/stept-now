"""SLA lifecycle: auto-apply on conversation.created, resolve/reopen transitions."""

from __future__ import annotations

from sqlalchemy import select

from app.models.sla import AppliedSla, SlaEvent, SlaStatus
from app.services import conversations as conversations_service
from app.services import slas as slas_service
from tests.slas.conftest import SYSTEM, SlaCtx, make_inbox, make_policy, make_workspace


async def _applied_for(session, conversation_id: str) -> AppliedSla | None:
    return (
        await session.execute(
            select(AppliedSla).where(AppliedSla.conversation_id == conversation_id)
        )
    ).scalar_one_or_none()


async def test_auto_apply_from_inbox_config(sla: SlaCtx):
    session, ws = sla.session, sla.workspace
    policy = await make_policy(session, ws, frt=15)
    inbox = await make_inbox(session, ws, config={"sla_policy_id": policy.id})

    conversation = await conversations_service.create_conversation(
        session, inbox=inbox, contact=sla.contact, actor=SYSTEM
    )

    applied = await _applied_for(session, conversation.id)
    assert applied is not None
    assert applied.sla_policy_id == policy.id
    assert applied.status == SlaStatus.ACTIVE.value


async def test_auto_apply_ignores_cross_workspace_policy(sla: SlaCtx):
    session, ws = sla.session, sla.workspace
    other_ws = await make_workspace(session, name="Other")
    foreign_policy = await make_policy(session, other_ws, frt=15)
    inbox = await make_inbox(session, ws, config={"sla_policy_id": foreign_policy.id})

    conversation = await conversations_service.create_conversation(
        session, inbox=inbox, contact=sla.contact, actor=SYSTEM
    )

    assert await _applied_for(session, conversation.id) is None


async def test_resolve_hit_then_reopen_active(sla: SlaCtx):
    session, ws = sla.session, sla.workspace
    policy = await make_policy(session, ws, frt=15)
    conversation = await conversations_service.create_conversation(
        session, inbox=sla.inbox, contact=sla.contact, actor=SYSTEM
    )
    applied = await slas_service.apply_sla(session, ws.id, conversation, policy.id, actor=SYSTEM)

    await conversations_service.update_status(session, conversation, "resolved", actor=SYSTEM)
    assert applied.status == SlaStatus.HIT.value
    assert applied.completed_at is not None

    await conversations_service.update_status(session, conversation, "open", actor=SYSTEM)
    assert applied.status == SlaStatus.ACTIVE.value
    assert applied.completed_at is None


async def test_resolve_missed_then_reopen_active_with_misses(sla: SlaCtx):
    session, ws = sla.session, sla.workspace
    policy = await make_policy(session, ws, frt=15)
    conversation = await conversations_service.create_conversation(
        session, inbox=sla.inbox, contact=sla.contact, actor=SYSTEM
    )
    applied = await slas_service.apply_sla(session, ws.id, conversation, policy.id, actor=SYSTEM)
    # Simulate an earlier recorded breach.
    session.add(
        SlaEvent(
            workspace_id=ws.id,
            applied_sla_id=applied.id,
            conversation_id=conversation.id,
            event_type="frt",
            meta={},
        )
    )
    applied.status = SlaStatus.ACTIVE_WITH_MISSES
    await session.flush()

    await conversations_service.update_status(session, conversation, "resolved", actor=SYSTEM)
    assert applied.status == SlaStatus.MISSED.value

    await conversations_service.update_status(session, conversation, "open", actor=SYSTEM)
    assert applied.status == SlaStatus.ACTIVE_WITH_MISSES.value
    assert applied.completed_at is None


async def test_replacing_policy_resets_application_and_events(sla: SlaCtx):
    session, ws = sla.session, sla.workspace
    first = await make_policy(session, ws, name="Gold", frt=15)
    second = await make_policy(session, ws, name="Silver", rt=240)
    conversation = await conversations_service.create_conversation(
        session, inbox=sla.inbox, contact=sla.contact, actor=SYSTEM
    )
    applied = await slas_service.apply_sla(session, ws.id, conversation, first.id, actor=SYSTEM)
    session.add(
        SlaEvent(
            workspace_id=ws.id,
            applied_sla_id=applied.id,
            conversation_id=conversation.id,
            event_type="frt",
            meta={},
        )
    )
    applied.status = SlaStatus.ACTIVE_WITH_MISSES
    await session.flush()

    replacement = await slas_service.apply_sla(
        session, ws.id, conversation, second.id, actor=SYSTEM
    )

    assert replacement.id == applied.id  # one row per conversation, replaced in place
    assert replacement.sla_policy_id == second.id
    assert replacement.status == SlaStatus.ACTIVE.value
    assert await slas_service.list_events(session, replacement.id) == []
