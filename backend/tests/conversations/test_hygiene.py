"""Conversation hygiene: auto-title, idle auto-resolve (+CSAT trigger), reopen.

Dogfood defects: every widget conversation had subject NULL (an inbox of
indistinguishable rows), agent-only threads sat pending forever, and resolving
an agent-handled thread never asked for a rating.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.db import utcnow
from app.core.events import Actor
from app.models.agent import Agent
from app.services import conversations as conversations_service
from app.services.conversations import (
    AUTO_RESOLVE_IDLE_HOURS,
    SUBJECT_MAX_CHARS,
    auto_resolve_idle,
    derive_subject,
)
from tests.conversations.conftest import SvcCtx, make_contact

CONTACT = Actor(type="contact", id=None, label="Nina Doe")


async def _new_conversation(svc: SvcCtx, **kwargs):
    return await conversations_service.create_conversation(
        session=svc.session,
        inbox=svc.inbox,
        contact=svc.contact,
        actor=CONTACT,
        **kwargs,
    )


async def _inbound(svc: SvcCtx, conversation, content: str):
    return await conversations_service.add_message(
        svc.session,
        conversation,
        direction="in",
        author_type="contact",
        author_id=svc.contact.id,
        author_name="Nina Doe",
        content=content,
        actor=CONTACT,
        deliver=False,
    )


# --- auto-title ---------------------------------------------------------------


def test_derive_subject_short_message_kept_verbatim():
    assert derive_subject("How do I set up an on-call rotation?") == (
        "How do I set up an on-call rotation?"
    )


def test_derive_subject_truncates_at_a_word_boundary():
    text = (
        "I have been trying to configure the escalation policy for our weekend "
        "rotation and nothing seems to save correctly"
    )
    subject = derive_subject(text)
    assert subject is not None and subject.endswith("…")
    assert len(subject) <= SUBJECT_MAX_CHARS + 1  # +1 for the ellipsis
    assert not subject.removesuffix("…").endswith(" ")
    # word boundary: the fragment before the ellipsis is a whole word from the text
    assert subject.removesuffix("…") in text


def test_derive_subject_strips_markdown_noise():
    assert derive_subject("## **Help!** `sync` is [broken](x)") == "Help! sync is broken x"
    assert derive_subject("   \n\n# \n") is None


async def test_first_visitor_message_titles_the_conversation(svc: SvcCtx):
    conversation = await _new_conversation(svc)
    assert conversation.subject is None
    await _inbound(svc, conversation, "How do I set up an on-call rotation?")
    assert conversation.subject == "How do I set up an on-call rotation?"

    # A later message must not retitle the thread.
    await _inbound(svc, conversation, "Something completely different")
    assert conversation.subject == "How do I set up an on-call rotation?"


async def test_explicit_subject_is_never_overwritten(svc: SvcCtx):
    conversation = await _new_conversation(svc, subject="Re: Invoice question")
    await _inbound(svc, conversation, "hello?")
    assert conversation.subject == "Re: Invoice question"


# --- auto-resolve + CSAT ------------------------------------------------------


async def _pending_agent_conversation(svc: SvcCtx, *, idle_hours: float, agent_id=None):
    conversation = await _new_conversation(svc)
    conversation.status = "pending"
    conversation.ai_agent_id = agent_id or "0" * 32
    conversation.last_activity_at = utcnow() - timedelta(hours=idle_hours)
    await svc.session.flush()
    return conversation


async def test_idle_agent_conversations_auto_resolve_and_trigger_csat(svc: SvcCtx):
    idle = await _pending_agent_conversation(svc, idle_hours=AUTO_RESOLVE_IDLE_HOURS + 1)
    fresh = await _pending_agent_conversation(svc, idle_hours=1)

    resolved = await auto_resolve_idle(svc.session)
    assert [c.id for c in resolved] == [idle.id]
    assert idle.status == "resolved"
    assert idle.resolved_at is not None
    assert idle.csat_requested is True, "resolving an agent-handled thread asks for a rating"
    assert fresh.status == "pending"


async def test_human_and_agentless_conversations_are_left_alone(svc: SvcCtx):
    open_conv = await _new_conversation(svc)
    open_conv.status = "open"
    open_conv.last_activity_at = utcnow() - timedelta(hours=100)
    agentless = await _new_conversation(svc)
    agentless.status = "pending"
    agentless.ai_agent_id = None
    agentless.last_activity_at = utcnow() - timedelta(hours=100)
    await svc.session.flush()

    assert await auto_resolve_idle(svc.session) == []
    assert open_conv.status == "open"
    assert agentless.status == "pending"


async def test_visitor_reply_reopens_an_auto_resolved_thread_to_the_agent(svc: SvcCtx):
    agent = Agent(workspace_id=svc.workspace.id, name="Sage", status="live")
    svc.session.add(agent)
    await svc.session.flush()
    conversation = await _pending_agent_conversation(
        svc, idle_hours=AUTO_RESOLVE_IDLE_HOURS + 1, agent_id=agent.id
    )
    await auto_resolve_idle(svc.session)
    assert conversation.status == "resolved"

    await _inbound(svc, conversation, "one more thing please")
    assert conversation.status == "pending", "the visitor's reply goes back to the bound agent"
    assert conversation.ai_agent_id == agent.id


async def test_human_resolve_of_agent_thread_also_triggers_csat_once(svc: SvcCtx):
    conversation = await _new_conversation(svc)
    conversation.ai_agent_id = "0" * 32
    await svc.session.flush()
    await conversations_service.update_status(
        svc.session, conversation, "resolved", actor=Actor(type="user", id=None, label="Sam")
    )
    assert conversation.csat_requested is True

    # Re-resolving later must not flip anything back or double-trigger.
    await conversations_service.update_status(
        svc.session, conversation, "open", actor=Actor(type="user", id=None, label="Sam")
    )
    await conversations_service.update_status(
        svc.session, conversation, "resolved", actor=Actor(type="user", id=None, label="Sam")
    )
    assert conversation.csat_requested is True


async def test_resolving_a_pure_human_thread_does_not_ask_for_csat(svc: SvcCtx):
    conversation = await _new_conversation(svc)
    assert conversation.ai_agent_id is None
    await conversations_service.update_status(
        svc.session, conversation, "resolved", actor=Actor(type="user", id=None, label="Sam")
    )
    assert conversation.csat_requested is False


async def test_auto_resolve_never_crosses_workspaces(svc: SvcCtx):
    """The sweep is global (scheduler job) but only ever flips rows that match
    the pending+agent+idle predicate — a foreign workspace's fresh thread must
    be untouched."""
    from tests.conversations.conftest import make_inbox, make_workspace

    other_ws = await make_workspace(svc.session, "Other WS")
    other_inbox = await make_inbox(svc.session, other_ws)
    other_contact = await make_contact(svc.session, other_ws)
    other = await conversations_service.create_conversation(
        session=svc.session,
        inbox=other_inbox,
        contact=other_contact,
        actor=CONTACT,
    )
    other.status = "pending"
    other.ai_agent_id = "0" * 32
    await svc.session.flush()  # fresh activity — not idle

    idle = await _pending_agent_conversation(svc, idle_hours=AUTO_RESOLVE_IDLE_HOURS + 1)
    resolved = await auto_resolve_idle(svc.session)
    assert [c.id for c in resolved] == [idle.id]
    assert other.status == "pending"
