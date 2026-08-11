"""Tour playback lifecycle → agent conversation glue.

Why this exists (the false-failure loop, doktrace 2026-08-11): when the agent
starts a tour with `show_guide`, the run parks `awaiting_client` and the ONLY
success signal was the messenger iframe POSTing `/copilot/result`. That round
trip is structurally lossy — the iframe drops results when the thread screen is
not active, its `pendingOps` map dies on every tour-step navigation, and the
replayed op after a reload reports to a screen nobody is on. The run then sat
parked until `sweep_stale_client_waits` wrote "timed out waiting for the page",
and the model apologized about a broken tour WHILE the tour played fine.

The loader's tour telemetry (`POST /api/widget/tours/{id}/events`) does not go
through the iframe at all and fires on every real lifecycle transition. This
module makes that channel authoritative:

- `started`   → resume the parked run with a success tool-result (the model can
                then say, truthfully, that the tour is playing);
- `completed` → mirror into the transcript + a short congrats from the agent,
                with a one-tap offer for the next obvious tour, if any;
- `dismissed` → record + mirror only. A person pressing Esc made a choice; the
                agent must not apologize, explain, or suggest a reload.

The link between a tour play and the conversation that initiated it lives on
`conversation.attributes["tour_run"]` (stamped by the engine when it parks the
`show_guide` op) — the established JSON-attributes pattern, no new tables.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import guides
from app.core.db import utcnow
from app.core.events import Actor
from app.core.i18n import DEFAULT_LOCALE, normalize_locale, translate
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.tour import Tour
from app.services import conversations as conversations_service

#: Where the engine stamps the "this conversation started that tour" link.
TOUR_RUN_ATTR = "tour_run"

#: Lifecycle events that reach the conversation. `step_viewed` / `step_blocked`
#: / `step_error` stay pure telemetry (health + funnel), never transcript.
_TRANSCRIPT_EVENTS = frozenset({"started", "completed", "dismissed"})

#: How many of the visitor's most recent conversations we scan for the link.
_LINK_SCAN_LIMIT = 10

#: Rough playback estimate for the offer card (per step).
EST_SECONDS_PER_STEP = 30


def offer_payload(*, tour_id: str, title: str, steps: int) -> dict[str, Any]:
    """The `tour_offer` attachment contract the messenger renders as a card."""
    return {
        "kind": "tour_offer",
        "tour_id": tour_id,
        "title": title,
        "steps": steps,
        "est_seconds": steps * EST_SECONDS_PER_STEP,
    }


def link_of(conversation: Conversation) -> dict[str, Any] | None:
    attributes = conversation.attributes if isinstance(conversation.attributes, dict) else {}
    link = attributes.get(TOUR_RUN_ATTR)
    return link if isinstance(link, dict) else None


def _conversation_locale(conversation: Conversation) -> str:
    attributes = conversation.attributes if isinstance(conversation.attributes, dict) else {}
    return normalize_locale(attributes.get("locale")) or DEFAULT_LOCALE


async def _linked_conversation(
    session: AsyncSession, workspace_id: str, tour_id: str, contact_id: str
) -> tuple[Conversation, dict[str, Any]] | None:
    """The contact's conversation whose agent started this tour, if any.

    Scans the contact's most recent threads and matches the JSON link in
    Python — portable across Postgres and SQLite, and the scan is tiny.
    """
    rows = (
        (
            await session.execute(
                select(Conversation)
                .where(
                    Conversation.workspace_id == workspace_id,
                    Conversation.contact_id == contact_id,
                )
                .order_by(Conversation.last_activity_at.desc(), Conversation.id.desc())
                .limit(_LINK_SCAN_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    for conversation in rows:
        link = link_of(conversation)
        if link and link.get("tour_id") == tour_id:
            return conversation, link
    return None


def _update_link(conversation: Conversation, link: dict[str, Any]) -> None:
    conversation.attributes = {**conversation.attributes, TOUR_RUN_ATTR: link}


async def _system_line(
    session: AsyncSession, conversation: Conversation, content: str, *, event: str, tour_id: str
) -> None:
    """A short, public, system-authored transcript line ("Tour completed: …").

    Same shape as the engine's handoff notice: the widget renders it centered,
    the dashboard sees an honest timeline. `meta.kind` lets both style it.
    """
    await conversations_service.add_message(
        session,
        conversation,
        direction="out",
        author_type="system",
        author_id=None,
        author_name="",
        content=content,
        visibility="public",
        meta={"kind": "tour_event", "event": event, "tour_id": tour_id},
        actor=Actor.system(),
        deliver=False,
    )


async def _post_congrats(
    session: AsyncSession, conversation: Conversation, tour: Tour, locale: str
) -> None:
    """Agent-authored congrats after `completed`, plus the next tour as an offer
    card when an obviously-related live tour exists."""
    agent: Agent | None = None
    if conversation.ai_agent_id:
        agent = await session.get(Agent, conversation.ai_agent_id)
        if agent is not None and agent.workspace_id != conversation.workspace_id:
            agent = None
    if agent is None:
        return  # no agent voice to speak with — the system mirror line suffices

    attributes = conversation.attributes if isinstance(conversation.attributes, dict) else {}
    url = attributes.get("page_url")
    next_match = await guides.next_tour_after(
        session, conversation.workspace_id, tour, url=url if isinstance(url, str) else None
    )
    text = translate("tour.congrats", locale, name=tour.name)
    attachments: list[dict[str, Any]] | None = None
    if next_match is not None:
        text = f"{text} {translate('tour.congrats_next', locale, name=next_match.name)}"
        attachments = [
            offer_payload(tour_id=next_match.id, title=next_match.name, steps=next_match.step_count)
        ]
    await conversations_service.add_message(
        session,
        conversation,
        direction="out",
        author_type="agent",
        author_id=agent.id,
        author_name=agent.name,
        content=text,
        attachments=attachments,
        meta={"kind": "tour_congrats", "tour_id": tour.id},
        actor=Actor(type="agent", id=agent.id, label=agent.name),
        deliver=False,
    )


async def process_event(
    session: AsyncSession,
    workspace_id: str,
    tour: Tour,
    *,
    event: str,
    contact_id: str | None,
    step_index: int | None = None,  # noqa: ARG001 — part of the seam's signature
    meta: dict[str, Any] | None = None,  # noqa: ARG001
) -> None:
    """React to one recorded playback event for an agent-initiated tour.

    No-ops fast for anything that is not a linked agent tour: anonymous plays,
    auto-delivered tours, dashboard previews. Never raises past the caller —
    `record_event` guards it so telemetry recording can never fail on this.
    """
    if event not in _TRANSCRIPT_EVENTS or not contact_id:
        return
    found = await _linked_conversation(session, workspace_id, tour.id, contact_id)
    if found is None:
        return
    conversation, link = found

    # 1) Truth for the parked run: the telemetry channel is authoritative.
    #    (The /copilot/result fast path may already have resumed it — fine.)
    run = await session.get(AgentRun, str(link.get("run_id") or ""))
    if run is not None and run.workspace_id == workspace_id:
        from app.agents import engine  # local: engine imports services, not us

        await engine.resolve_guide_wait(session, run, event=event, tour_id=tour.id)

    # 2) Truthful transcript, once per lifecycle stage per link.
    mirrored = dict(link.get("mirrored") or {})
    if event in mirrored:
        return
    mirrored[event] = utcnow().isoformat()
    _update_link(conversation, {**link, "mirrored": mirrored, "last_event": event})

    locale = _conversation_locale(conversation)
    if event == "started":
        # The engine already posted "Starting tour …" when it dispatched the
        # op — a second line seconds later would be noise, not truth.
        if not link.get("announced"):
            await _system_line(
                session,
                conversation,
                translate("tour.transcript.started", locale, name=tour.name),
                event=event,
                tour_id=tour.id,
            )
        return
    if event == "dismissed":
        # A deliberate Esc. Record it, say it happened, and say NOTHING else.
        await _system_line(
            session,
            conversation,
            translate("tour.transcript.dismissed", locale, name=tour.name),
            event=event,
            tour_id=tour.id,
        )
        return
    # completed
    await _system_line(
        session,
        conversation,
        translate("tour.transcript.completed", locale, name=tour.name),
        event=event,
        tour_id=tour.id,
    )
    await _post_congrats(session, conversation, tour, locale)
