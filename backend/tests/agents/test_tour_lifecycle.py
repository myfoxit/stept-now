"""Tour playback lifecycle → agent conversation truth.

The false-failure loop: `show_guide` parked the run and the ONLY success signal
was the messenger iframe's `/copilot/result` round trip, which is lossy — so the
sweep reported "timed out" while the tour played fine, and an Esc came back as a
failure. These tests pin the fix: the loader's tour TELEMETRY channel
(`POST /api/widget/tours/{id}/events`) is authoritative for the parked run and
for the transcript.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import session_scope
from app.core.security import create_widget_token
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.message import Message
from tests.agents.conftest import (
    conversation_with_message,
    get_run,
    get_steps,
    make_agent,
    public_messages,
    run_now,
)
from tests.agents.test_page_tools import PAGE_CONTROL, allow_page_control, make_tour
from tests.conftest import drain_tasks


async def _widget_key(inbox_id: str) -> str:
    async with session_scope() as session:
        inbox = await session.get(Inbox, inbox_id)
        assert inbox is not None and inbox.widget_key
        return inbox.widget_key


async def _post_event(client, actx, tour_id: str, event: str, *, contact_id: str | None = None):
    key = await _widget_key(actx.inbox_id)
    headers = {
        "X-Widget-Token": create_widget_token(actx.workspace_id, contact_id or actx.contact_id)
    }
    return await client.post(
        f"/api/widget/tours/{tour_id}/events?widget_key={key}",
        json={"event": event},
        headers=headers,
    )


async def _parked_guide_run(actx, tour_id: str) -> tuple[str, str]:
    """An agent-started tour: run parked awaiting_client on the guide op."""
    agent_id = await make_agent(actx, settings=PAGE_CONTROL)
    conversation_id, message_id = await conversation_with_message(
        actx, f'Show me the tour [[tool:show_guide {{"tour_id": "{tour_id}"}}]]'
    )
    await allow_page_control(conversation_id)
    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        conversation.ai_agent_id = agent_id  # congrats speaks with the bound agent's voice
        await session.commit()
    run_id = await run_now(actx, agent_id, conversation_id, trigger_message_id=message_id)
    assert (await get_run(run_id)).status == "awaiting_client"
    return run_id, conversation_id


async def _tour_event_lines(conversation_id: str) -> list[dict]:
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.visibility == "public",
                        Message.author_type == "system",
                    )
                    .order_by(Message.created_at, Message.id)
                )
            )
            .scalars()
            .all()
        )
        return [
            {"content": m.content, **(m.meta or {})}
            for m in rows
            if (m.meta or {}).get("kind") == "tour_event"
        ]


# --- link + announce at park time -------------------------------------------


async def test_parking_a_guide_op_stamps_the_link_and_announces(actx):
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    run_id, conversation_id = await _parked_guide_run(actx, tour_id)

    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        link = conversation.attributes.get("tour_run")
    assert link and link["tour_id"] == tour_id and link["run_id"] == run_id
    assert link["op_id"]

    lines = await _tour_event_lines(conversation_id)
    assert [line["event"] for line in lines] == ["starting"]
    assert "Create an invoice" in lines[0]["content"]


# --- started: the authoritative success signal -------------------------------


async def test_started_telemetry_resumes_the_parked_run_with_success(actx, client):
    """The exact doktrace failure: the widget's /copilot/result was lost, and 90s
    later the model apologized about a tour that was playing. The `started`
    event must resume the run with a SUCCESS result instead."""
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    run_id, conversation_id = await _parked_guide_run(actx, tour_id)

    resp = await _post_event(client, actx, tour_id, "started")
    assert resp.status_code == 200, resp.text
    await drain_tasks()

    run = await get_run(run_id)
    assert run.status == "completed"
    assert run.pending_tool_call is None
    result_step = next(step for step in await get_steps(run_id) if step.kind == "tool_result")
    assert result_step.output.get("ok") is True
    assert "error" not in result_step.output

    # The model replied after the confirmed start — the visitor is not told to
    # reload a page that is working.
    replies = [m for m in await public_messages(conversation_id) if m.author_type == "agent"]
    assert replies, "a reply must follow the confirmed start"

    # No duplicate "started" system line: the park-time announce covered it.
    events = [line["event"] for line in await _tour_event_lines(conversation_id)]
    assert events == ["starting"]


# --- dismissed: a choice, never a failure ------------------------------------


async def test_dismissed_while_parked_finalizes_silently(actx, client):
    """Esc is a decision, not an error. The run ends without ANY agent reply —
    no apology, no reload advice — and the transcript records the dismissal."""
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    run_id, conversation_id = await _parked_guide_run(actx, tour_id)

    resp = await _post_event(client, actx, tour_id, "dismissed")
    assert resp.status_code == 200, resp.text
    await drain_tasks()

    run = await get_run(run_id)
    assert run.status == "completed"
    assert run.pending_tool_call is None
    result_step = next(step for step in await get_steps(run_id) if step.kind == "tool_result")
    assert result_step.output.get("outcome") == "dismissed"
    assert result_step.output.get("ok") is True

    agent_messages = [m for m in await public_messages(conversation_id) if m.author_type == "agent"]
    assert agent_messages == [], "dismissal must produce NO agent reply at all"
    events = [line["event"] for line in await _tour_event_lines(conversation_id)]
    assert events == ["starting", "dismissed"]


# --- completed: congrats + next tour ----------------------------------------


async def test_completed_mirrors_and_congratulates_with_next_tour_offer(actx, client):
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    next_id = await make_tour(actx.workspace_id, name="Send an invoice reminder")
    run_id, conversation_id = await _parked_guide_run(actx, tour_id)

    assert (await _post_event(client, actx, tour_id, "started")).status_code == 200
    await drain_tasks()
    assert (await get_run(run_id)).status == "completed"

    assert (await _post_event(client, actx, tour_id, "completed")).status_code == 200
    await drain_tasks()

    events = [line["event"] for line in await _tour_event_lines(conversation_id)]
    assert events == ["starting", "completed"]

    congrats = [
        m
        for m in await public_messages(conversation_id)
        if m.author_type == "agent" and (m.meta or {}).get("kind") == "tour_congrats"
    ]
    assert len(congrats) == 1
    assert "Create an invoice" in congrats[0].content
    offers = [a for a in congrats[0].attachments if a.get("kind") == "tour_offer"]
    assert len(offers) == 1
    assert offers[0]["tour_id"] == next_id
    assert offers[0]["title"] == "Send an invoice reminder"
    assert offers[0]["steps"] >= 1 and offers[0]["est_seconds"] > 0


async def test_completed_without_related_tour_congratulates_without_offer(actx, client):
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    await make_tour(actx.workspace_id, name="Team permissions")  # unrelated — must not be offered
    run_id, conversation_id = await _parked_guide_run(actx, tour_id)
    assert (await _post_event(client, actx, tour_id, "started")).status_code == 200
    await drain_tasks()
    assert (await get_run(run_id)).status == "completed"

    assert (await _post_event(client, actx, tour_id, "completed")).status_code == 200
    await drain_tasks()

    congrats = [
        m
        for m in await public_messages(conversation_id)
        if (m.meta or {}).get("kind") == "tour_congrats"
    ]
    assert len(congrats) == 1
    assert congrats[0].attachments == []


# --- scoping ------------------------------------------------------------------


async def test_another_contacts_event_cannot_resume_the_run(actx, client):
    tour_id = await make_tour(actx.workspace_id, name="Create an invoice")
    run_id, conversation_id = await _parked_guide_run(actx, tour_id)
    async with session_scope() as session:
        other = Contact(workspace_id=actx.workspace_id, name="Other Visitor")
        session.add(other)
        await session.commit()
        other_id = other.id

    resp = await _post_event(client, actx, tour_id, "started", contact_id=other_id)
    assert resp.status_code == 200  # telemetry records fine
    await drain_tasks()

    assert (await get_run(run_id)).status == "awaiting_client", (
        "someone else's playback must not resume this visitor's run"
    )
    events = [line["event"] for line in await _tour_event_lines(conversation_id)]
    assert events == ["starting"], "and must not write into their transcript"


async def test_unlinked_tour_play_is_pure_telemetry(actx, client):
    """Auto-delivered (non-agent) tour plays record events and touch nothing."""
    tour_id = await make_tour(actx.workspace_id, name="Standalone tour")
    resp = await _post_event(client, actx, tour_id, "started")
    assert resp.status_code == 200
    assert resp.json()["message"] == "recorded"
    resp = await _post_event(client, actx, tour_id, "dismissed")
    assert resp.status_code == 200
