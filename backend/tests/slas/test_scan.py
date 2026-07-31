"""Breach scan matrix: frt once, nrt per waiting episode, rt, notifications.

The scan runs in its own unit of work (session_scope), so tests commit their
setup, call `scan_sla_breaches(now=...)` with a pinned clock, then re-query.
Plain-string ids are captured up front — helpers expire the session, so ORM
objects are re-fetched rather than reused across scans.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.core.db import utcnow
from app.models.notification import Notification
from app.models.sla import AppliedSla, SlaEvent, SlaStatus
from app.services import conversations as conversations_service
from app.services import slas as slas_service
from tests.slas.conftest import SYSTEM, SlaCtx, make_policy


async def _events(session, conversation_id: str) -> list[SlaEvent]:
    session.expire_all()
    result = await session.execute(
        select(SlaEvent)
        .where(SlaEvent.conversation_id == conversation_id)
        .order_by(SlaEvent.created_at, SlaEvent.id)
    )
    return list(result.scalars())


async def _applied_status(session, conversation_id: str) -> str:
    session.expire_all()
    applied = (
        await session.execute(
            select(AppliedSla).where(AppliedSla.conversation_id == conversation_id)
        )
    ).scalar_one()
    return applied.status


async def _agent_reply(session, workspace_id: str, conversation_id: str, user_id: str) -> None:
    conversation = await conversations_service.get_conversation(
        session, workspace_id, conversation_id
    )
    await conversations_service.add_message(
        session,
        conversation,
        direction="out",
        author_type="user",
        author_id=user_id,
        author_name="Sam Support",
        content="On it!",
        actor=SYSTEM,
        deliver=False,
    )


async def _inbound(session, workspace_id: str, conversation_id: str, contact_id: str) -> None:
    conversation = await conversations_service.get_conversation(
        session, workspace_id, conversation_id
    )
    await conversations_service.add_message(
        session,
        conversation,
        direction="in",
        author_type="contact",
        author_id=contact_id,
        author_name="Nina Doe",
        content="Any update?",
        actor=SYSTEM,
        deliver=False,
    )


async def _waiting_since(session, workspace_id: str, conversation_id: str):
    conversation = await conversations_service.get_conversation(
        session, workspace_id, conversation_id
    )
    return conversation.waiting_since


async def test_frt_breach_fires_once_and_notifies_assignee(sla: SlaCtx):
    session = sla.session
    workspace_id, user_id = sla.workspace.id, sla.user.id
    policy = await make_policy(session, sla.workspace, frt=15)
    conversation = await conversations_service.create_conversation(
        session, inbox=sla.inbox, contact=sla.contact, actor=SYSTEM
    )
    conversation_id = conversation.id
    conversation.assignee_user_id = user_id
    await slas_service.apply_sla(session, workspace_id, conversation, policy.id, actor=SYSTEM)
    await session.commit()

    # Not yet due: nothing recorded.
    assert await slas_service.scan_sla_breaches(now=utcnow() + timedelta(minutes=10)) == 0

    deadline_passed = utcnow() + timedelta(minutes=16)
    assert await slas_service.scan_sla_breaches(now=deadline_passed) == 1
    # Idempotent: a second scan past the deadline records nothing new.
    assert await slas_service.scan_sla_breaches(now=deadline_passed) == 0

    events = await _events(session, conversation_id)
    assert [event.event_type for event in events] == ["frt"]
    assert await _applied_status(session, conversation_id) == SlaStatus.ACTIVE_WITH_MISSES.value

    notifications = list(
        (
            await session.execute(select(Notification).where(Notification.user_id == user_id))
        ).scalars()
    )
    assert len(notifications) == 1
    assert notifications[0].type == "sla"
    assert notifications[0].title == "SLA FRT missed"
    assert notifications[0].link == f"/inbox/{conversation_id}"


async def test_nrt_fires_once_per_waiting_episode(sla: SlaCtx):
    session = sla.session
    workspace_id, user_id, contact_id = sla.workspace.id, sla.user.id, sla.contact.id
    policy = await make_policy(session, sla.workspace, nrt=10)
    conversation = await conversations_service.create_conversation(
        session, inbox=sla.inbox, contact=sla.contact, actor=SYSTEM
    )
    conversation_id = conversation.id
    await slas_service.apply_sla(session, workspace_id, conversation, policy.id, actor=SYSTEM)

    # First episode: agent replied (first_reply_at set), then the contact waits.
    await _agent_reply(session, workspace_id, conversation_id, user_id)
    await _inbound(session, workspace_id, conversation_id, contact_id)
    first_episode = await _waiting_since(session, workspace_id, conversation_id)
    assert first_episode is not None
    await session.commit()

    late = utcnow() + timedelta(minutes=11)
    assert await slas_service.scan_sla_breaches(now=late) == 1
    assert await slas_service.scan_sla_breaches(now=late) == 0  # same episode: once

    # Agent reply clears waiting_since; a new inbound starts a new episode.
    await _agent_reply(session, workspace_id, conversation_id, user_id)
    assert await _waiting_since(session, workspace_id, conversation_id) is None
    await _inbound(session, workspace_id, conversation_id, contact_id)
    second_episode = await _waiting_since(session, workspace_id, conversation_id)
    assert second_episode is not None and second_episode != first_episode
    await session.commit()

    assert await slas_service.scan_sla_breaches(now=utcnow() + timedelta(minutes=11)) == 1

    events = await _events(session, conversation_id)
    assert [event.event_type for event in events] == ["nrt", "nrt"]
    episodes = {event.meta["waiting_since"] for event in events}
    assert episodes == {first_episode.isoformat(), second_episode.isoformat()}


async def test_rt_breach_recorded_until_resolution(sla: SlaCtx):
    session = sla.session
    workspace_id = sla.workspace.id
    policy = await make_policy(session, sla.workspace, rt=60)
    conversation = await conversations_service.create_conversation(
        session, inbox=sla.inbox, contact=sla.contact, actor=SYSTEM
    )
    conversation_id = conversation.id
    await slas_service.apply_sla(session, workspace_id, conversation, policy.id, actor=SYSTEM)
    await session.commit()

    late = utcnow() + timedelta(minutes=61)
    assert await slas_service.scan_sla_breaches(now=late) == 1
    assert await slas_service.scan_sla_breaches(now=late) == 0

    events = await _events(session, conversation_id)
    assert [event.event_type for event in events] == ["rt"]

    # Resolving a breached application finalizes it as missed and stops scans.
    conversation = await conversations_service.get_conversation(
        session, workspace_id, conversation_id
    )
    await conversations_service.update_status(session, conversation, "resolved", actor=SYSTEM)
    await session.commit()
    assert await _applied_status(session, conversation_id) == SlaStatus.MISSED.value
    assert await slas_service.scan_sla_breaches(now=utcnow() + timedelta(minutes=120)) == 0


async def test_frt_not_recorded_after_first_reply(sla: SlaCtx):
    session = sla.session
    workspace_id, user_id = sla.workspace.id, sla.user.id
    policy = await make_policy(session, sla.workspace, frt=15)
    conversation = await conversations_service.create_conversation(
        session, inbox=sla.inbox, contact=sla.contact, actor=SYSTEM
    )
    conversation_id = conversation.id
    await slas_service.apply_sla(session, workspace_id, conversation, policy.id, actor=SYSTEM)
    await _agent_reply(session, workspace_id, conversation_id, user_id)  # replied within target
    await session.commit()

    assert await slas_service.scan_sla_breaches(now=utcnow() + timedelta(minutes=90)) == 0
    assert await _events(session, conversation_id) == []
