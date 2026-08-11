"""`tour_autostart_policy` (widget inbox settings JSON): "ask" | "auto" | "never".

Dogfood defect: an informational question ("How do I set up an on-call
rotation?") auto-played a tour with zero text. Under the default "ask" policy
the agent answers in words and attaches a `{"kind": "tour_offer"}` card the
messenger renders; only an explicit imperative ("show me…", "play that tour
again") starts playback immediately — the b61f9a8 replay case, which must keep
working.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agents.guides import is_tour_imperative
from app.core.db import session_scope
from app.models.inbox import Inbox
from app.models.message import Message
from tests.agents.conftest import (
    conversation_with_message,
    get_run,
    get_steps,
    make_agent,
    public_messages,
    run_now,
    step_kinds,
)
from tests.agents.test_page_tools import PAGE_CONTROL, allow_page_control, make_tour

# --- the imperative gate ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Show me that tour again",
        "show me how to create an invoice",
        "Can you walk me through creating an invoice?",
        "Please play the tour again",
        "start the on-call tour",
        "replay the walkthrough",
        "Zeig mir das nochmal",
        "Zeige mir bitte die Tour",
        "Spiel die Tour nochmal ab",
        "Starte die Anleitung erneut",
        "Montre-moi la visite",
        "Muéstrame el recorrido",
        "ツアーをもう一度見せて",
    ],
)
def test_imperatives_detected(text):
    assert is_tour_imperative(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "How do I set up an on-call rotation?",
        "What is the difference between a problem and an alert?",
        "Wie richte ich eine Rufbereitschaft ein?",
        "How do I start a subscription?",
        "where can I find my invoices",
        "the tour yesterday was helpful, thanks",
        "",
        None,
    ],
)
def test_informational_questions_are_not_imperatives(text):
    assert is_tour_imperative(text) is False


# --- helpers ------------------------------------------------------------------


async def _set_policy(actx, policy: str | None) -> None:
    async with session_scope() as session:
        inbox = await session.get(Inbox, actx.inbox_id)
        config = dict(inbox.config or {})
        if policy is None:
            config.pop("tour_autostart_policy", None)
        else:
            config["tour_autostart_policy"] = policy
        inbox.config = config
        await session.commit()


async def _agent_reply(conversation_id: str) -> Message:
    async with session_scope() as session:
        return (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.author_type == "agent",
                    Message.visibility == "public",
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1)
            )
        ).scalar_one()


INFORMATIONAL = 'How do I create an invoice? [[tool:show_guide {{"tour_id": "{tour_id}"}}]]'
IMPERATIVE = 'Show me the invoice tour [[tool:show_guide {{"tour_id": "{tour_id}"}}]]'


# --- "ask" (the default) ------------------------------------------------------


async def test_ask_intercepts_autostart_into_an_offer_card(actx):
    """Informational question + a model that reaches for show_guide → text
    answer with a tour_offer attachment, tour NOT started."""
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, INFORMATIONAL.format(tour_id=tour_id)
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    run = await get_run(run_id)
    assert run.status == "completed", "the run must not park on the page"
    kinds = await step_kinds(run_id)
    assert "client_request" not in kinds, "nothing was pushed to the browser"
    result = next(step for step in await get_steps(run_id) if step.kind == "tool_result")
    assert result.output.get("offer_attached") is True

    reply = await _agent_reply(conversation_id)
    offers = [a for a in reply.attachments if a.get("kind") == "tour_offer"]
    assert len(offers) == 1
    assert offers[0] == {
        "kind": "tour_offer",
        "tour_id": tour_id,
        "title": "Create an invoice",
        "steps": 1,
        "est_seconds": 30,
    }


async def test_ask_is_the_default_policy(actx):
    """No config key at all behaves as "ask" — the doktrace inbox has none."""
    await _set_policy(actx, None)
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, INFORMATIONAL.format(tour_id=tour_id)
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    assert (await get_run(run_id)).status == "completed"
    assert "client_request" not in await step_kinds(run_id)


async def test_ask_starts_immediately_on_explicit_imperative(actx):
    """ "Show me …" is the b61f9a8 case and must keep starting the tour — plus
    the visitor sees a "Starting tour" line, so the takeover is explained."""
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, IMPERATIVE.format(tour_id=tour_id)
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    run = await get_run(run_id)
    assert run.status == "awaiting_client"
    request = next(step for step in await get_steps(run_id) if step.kind == "client_request")
    assert request.output["op"] == "guide"
    assert request.output["args"]["tour_id"] == tour_id

    system_lines = [
        m
        for m in await public_messages(conversation_id)
        if m.author_type == "system" and (m.meta or {}).get("kind") == "tour_event"
    ]
    assert len(system_lines) == 1
    assert "Create an invoice" in system_lines[0].content


async def test_ask_attaches_find_guide_candidate_when_model_answers_in_text(actx):
    """Even when the model never calls show_guide, an answered question still
    carries its best tour as a card (the find_guide candidate path)."""
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, 'How do I create an invoice? [[tool:find_guide {"query": "create an invoice"}]]'
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    assert (await get_run(run_id)).status == "completed"
    reply = await _agent_reply(conversation_id)
    offers = [a for a in reply.attachments if a.get("kind") == "tour_offer"]
    assert [offer["tour_id"] for offer in offers] == [tour_id]


# --- "auto" -------------------------------------------------------------------


async def test_auto_policy_keeps_legacy_autostart(actx):
    await _set_policy(actx, "auto")
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, INFORMATIONAL.format(tour_id=tour_id)
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    assert (await get_run(run_id)).status == "awaiting_client"


# --- "never" ------------------------------------------------------------------


async def test_never_policy_withholds_show_guide_entirely(actx):
    await _set_policy(actx, "never")
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx,
        IMPERATIVE.format(tour_id=tour_id),  # even an imperative cannot start one
    )
    await allow_page_control(conversation_id)
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)

    run = await get_run(run_id)
    assert run.status == "completed"
    kinds = await step_kinds(run_id)
    assert "client_request" not in kinds
    # The withheld tool was never even called (the mock only calls offered tools).
    assert not any(
        step.name == "show_guide" for step in await get_steps(run_id) if step.kind == "tool_call"
    )
    reply = await _agent_reply(conversation_id)
    assert [a for a in reply.attachments if a.get("kind") == "tour_offer"] == []
