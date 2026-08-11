"""The agent execution engine: a DB-backed defer/resume tool loop.

State machine (docs/research/claude-agent-sdk.md §5.1):

    queued ─▶ running ─▶ completed          (final reply posted)
                    │
                    ├─▶ handed_off          (handoff tool / loop-exhaustion / empty reply)
                    ├─▶ failed              (non-retryable provider error)
                    ├─▶ awaiting_approval ──(human decision)──▶ running ─▶ …
                    └─▶ awaiting_client ────(widget result)───▶ running ─▶ …

When a ``require_approval`` tool is reached the loop persists an
``ApprovalRequest`` + ``pending_tool_call`` + a serialized ``messages_snapshot``,
flips the run to ``awaiting_approval`` and RETURNS — nothing is kept in memory, so
the gate survives a process restart. A human decision re-enqueues the run; the
resume rehydrates the snapshot, executes (approve) or denies-as-tool-result
(reject/expire — the model sees the denial and adapts, per docs/research/vercel-ai.md),
then continues the loop.

``awaiting_client`` is the same mechanism with a browser instead of a human on
the other side: an in-app page tool (`app.agents.page_tools`) cannot run on the
server, so the call is persisted, pushed to the visitor's widget over the
conversation topic, and the widget POSTs the result back to resume. A visitor who
closes the tab mid-guide leaves a run parked in ``awaiting_client``; the
``sweep_stale_client_waits`` job hands those to a human instead of leaking them.

A conversation is NEVER stranded: any provider failure, loop exhaustion, or empty
reply falls back to a human (status ``open`` + activity note).

Event triggers (@on, registered at import) wire the engine into conversations.
The side-effect import in ``app/api/v1/agents.py`` guarantees registration at app
build. ``app.agents.tasks`` (imported at the bottom) registers the queue task.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import event as event_
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import client_actions, guides, page_tools, tour_events
from app.agents import tools as tool_registry
from app.agents.tools import (
    ACTION_DEFAULT_POLICY,
    DEFAULT_POLICIES,
    POLICY_DISABLED,
    POLICY_REQUIRE_APPROVAL,
    ToolContext,
    ToolOutcome,
)
from app.ai.base import ChatMessage, ChatRequest, ProviderError, ToolCall
from app.ai.registry import resolve_chat
from app.core.db import utcnow, uuid7
from app.core.errors import ConflictError
from app.core.events import Actor, Event, EventNames, emit, on
from app.core.i18n import DEFAULT_LOCALE, LOCALES, normalize_locale, translate
from app.core.permissions import Perm, resolve_permissions
from app.core.queue import enqueue
from app.core.scheduler import scheduled
from app.models.agent import Agent
from app.models.agent_run import AgentRun, AgentStep, ApprovalRequest
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.message import Message
from app.models.workspace import Membership, Workspace
from app.realtime.manager import broadcast, conversation_topic, workspace_topic
from app.services import conversations as conversations_service
from app.services.language import detect_language
from app.services.notifications import notify

LEASE_SECONDS = 120
APPROVAL_TTL_HOURS = 24
HISTORY_CAP = 30
#: How long a run may sit waiting for the visitor's browser before we give up on
#: it. Generous enough for a slow page + a `page_wait`, short enough that a
#: closed tab does not leave the person staring at a silent thread.
CLIENT_OP_TIMEOUT_SECONDS = 90
#: A parked *client action* may be sitting behind a confirm card, i.e. waiting on
#: a person reading it — that deserves minutes, not the 90s a DOM op gets. The
#: run is durable state either way; nothing is held while it waits.
ACTION_CONFIRM_TIMEOUT_SECONDS = 600
#: A queued run older than this that nobody executed (lost enqueue, or a
#: follow-up waiting behind a finished chain) is re-enqueued by the reaper.
QUEUED_STALL_SECONDS = 120
#: Grace after lease expiry before the reaper re-enqueues a "running" run —
#: the claim/flush of a healthy worker must never race the reaper.
LEASE_REAP_GRACE_SECONDS = 30
_TERMINAL_STATUSES = frozenset({"completed", "failed", "handed_off", "canceled"})
_ACTIVE_STATUSES = ("queued", "running", "awaiting_approval", "awaiting_client")
_CITATION_RE = re.compile(r"\[(\d+)\]")

# --- tour autostart policy (widget inbox config `tour_autostart_policy`) -----
TOUR_POLICY_ASK = "ask"
TOUR_POLICY_AUTO = "auto"
TOUR_POLICY_NEVER = "never"
_TOUR_POLICIES = frozenset({TOUR_POLICY_ASK, TOUR_POLICY_AUTO, TOUR_POLICY_NEVER})
#: Conversation-attribute keys for the deferred tour-offer card. `tour_offer`
#: is what an intercepted `show_guide` chose; the candidate is the best
#: `find_guide` hit, used when the model never tried to start one.
TOUR_OFFER_ATTR = "tour_offer"
TOUR_OFFER_CANDIDATE_ATTR = "tour_offer_candidate"


class _ResumeNotReady(Exception):
    """Raised when a resume is enqueued before the decision commits — queue retries."""


@dataclass
class ExecutionResult:
    status: str
    reply: str | None
    steps: list[AgentStep]
    citations: list[dict]


@dataclass
class _StepSink:
    session: AsyncSession
    run: AgentRun
    mode: str
    next_ord: int
    sandbox_steps: list[AgentStep] = field(default_factory=list)

    async def add(
        self,
        kind: str,
        *,
        name: str | None = None,
        input: dict | None = None,
        output: dict | None = None,
        latency_ms: int | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> AgentStep:
        step = AgentStep(
            id=uuid7(),
            workspace_id=self.run.workspace_id,
            run_id=self.run.id,
            ord=self.next_ord,
            kind=kind,
            name=name,
            input=input or {},
            output=output or {},
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            created_at=utcnow(),
        )
        self.next_ord += 1
        if self.mode == "sandbox":
            self.sandbox_steps.append(step)
        else:
            self.session.add(step)
            await self.session.flush()
        return step


# ---------------------------------------------------------------------------
# message (de)serialization for pause/resume
# ---------------------------------------------------------------------------


def serialize_messages(messages: list[ChatMessage]) -> list[dict]:
    return [
        {
            "role": message.role,
            "content": message.content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "input": tc.input, "raw_input": tc.raw_input}
                for tc in message.tool_calls
            ],
            "tool_call_id": message.tool_call_id,
            "is_error": message.is_error,
        }
        for message in messages
    ]


def deserialize_messages(data: list | None) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for item in data or []:
        messages.append(
            ChatMessage(
                role=item["role"],
                content=item.get("content"),
                tool_calls=[
                    ToolCall(
                        id=tc["id"],
                        name=tc["name"],
                        input=tc.get("input") or {},
                        raw_input=tc.get("raw_input"),
                    )
                    for tc in item.get("tool_calls", [])
                ],
                tool_call_id=item.get("tool_call_id"),
                is_error=item.get("is_error", False),
            )
        )
    return messages


# ---------------------------------------------------------------------------
# prompt / history / citations
# ---------------------------------------------------------------------------


def compose_system_prompt(
    agent: Agent,
    workspace_name: str,
    *,
    conversation: Conversation | None = None,
    plan: tool_registry.ToolPlan | None = None,
    reply_locale: str | None = None,
    reply_locale_source: str = "stored",
    tour_policy: str = TOUR_POLICY_AUTO,
    tour_imperative: bool = False,
) -> str:
    settings = agent.settings if isinstance(agent.settings, dict) else {}
    parts: list[str] = []
    base = (agent.system_prompt or "").strip()
    parts.append(base or f"You are {agent.name}, a helpful AI support agent.")
    parts.append(f"You are helping customers of {workspace_name}.")
    parts.append(
        "You may use tools to search the knowledge base, tag or close the conversation, "
        "collect contact details, leave an internal note, or hand off to a human. "
        "Use at most one tool per step and prefer answering from the knowledge base."
    )
    retrieval = settings.get("retrieval") or {}
    if retrieval.get("enabled", True):
        parts.append(
            "When you use information from the knowledge base, cite each source inline "
            "as [n], matching the numbered search results."
        )
    guardrails = settings.get("guardrails") or {}
    if guardrails.get("require_citations"):
        parts.append(
            "Only state facts you can cite from a knowledge-base source; if you cannot, "
            "hand off to a human instead of guessing."
        )
    parts.append(_language_prompt(settings, reply_locale, reply_locale_source))
    parts.extend(
        _page_control_prompt(
            conversation, plan, tour_policy=tour_policy, tour_imperative=tour_imperative
        )
    )
    return "\n\n".join(parts)


def _conversation_locale(conversation: Conversation | None) -> str | None:
    """Language stamped on the thread by `app.services.language`, if any."""
    if conversation is None or not isinstance(conversation.attributes, dict):
        return None
    return normalize_locale(conversation.attributes.get("locale"))


def _language_prompt(
    settings: dict[str, Any], reply_locale: str | None, source: str = "stored"
) -> str:
    """Tell the model which language to answer in.

    The rule (W13): reply in the language of the customer's LATEST message.
    When per-message detection is confident (`source="detected"`), that wins
    outright — a contact with months of German history who writes one English
    question gets an English answer. The stored contact/conversation locale is
    only a tiebreak for messages too short to carry evidence ("ok", "thanks"),
    and even then the model is told to follow the customer if they switch.

    A workspace that must answer in one fixed language — a regulated market, a
    team that only reads Japanese — sets `settings.reply_language`.
    """
    configured = normalize_locale(settings.get("reply_language"))
    if configured:
        name = LOCALES[configured].english_name
        return (
            f"Always reply in {name}, whatever language the customer writes in. "
            f"If they write in another language, still answer in {name}."
        )
    if reply_locale and reply_locale in LOCALES:
        name = LOCALES[reply_locale].english_name
        if source == "detected":
            return (
                f"The customer's most recent message is written in {name}. Reply in {name}. "
                "Always match the language of their LATEST message — even when earlier "
                "messages in this conversation were written in a different language."
            )
        return (
            f"This customer has been writing in {name}, so reply in {name}. "
            "If they switch languages, follow them — always match the language "
            "of their most recent message."
        )
    return (
        "Reply in the same language the customer writes in, matching their most "
        "recent message. Never answer in English just because these instructions "
        "are in English — and never default to the language your persona or "
        "system instructions happen to be written in: the customer's own words "
        "are the only language signal that counts."
    )


def _page_control_prompt(
    conversation: Conversation | None,
    plan: tool_registry.ToolPlan | None,
    *,
    tour_policy: str = TOUR_POLICY_AUTO,
    tour_imperative: bool = False,
) -> list[str]:
    """The in-app-guidance half of the prompt.

    Only emitted when the page tools are actually in the plan — a model told it can
    walk someone through the UI, that then has no such tool, produces confident
    promises it cannot keep. The ladder (show a tour → show your own steps → do it)
    is spelled out because the default failure mode is the opposite: a model that
    explains in prose when it could point, and clicks when it should have asked.

    The first rung bends to `tour_autostart_policy`: under "ask" an informational
    question gets a text answer plus an offer card (never a hijacked screen);
    under "never" the agent cannot start tours at all.
    """
    if plan is None or not plan.client:
        return []
    parts: list[str] = []
    where = _page_context_line(conversation)
    if where:
        parts.append(where)
    if tour_policy == TOUR_POLICY_NEVER:
        tour_rung = (
            "1. call find_guide — if a published tour covers it, describe its steps in "
            "words (you cannot start tours in this workspace);"
        )
    elif tour_policy == TOUR_POLICY_ASK and not tour_imperative:
        tour_rung = (
            "1. call find_guide — if a published tour covers it, ANSWER THE QUESTION IN "
            "WORDS first, then call show_guide once: it will not start the tour, it "
            "attaches a one-tap offer card to your reply so the person can start it "
            "themselves. Never claim a tour is playing;"
        )
    else:
        tour_rung = (
            "1. call find_guide — if a published tour covers it, play it with show_guide and "
            "say in one line what it will walk them through;"
        )
    ladder = [
        "You are embedded IN the app the person is using, so you can show them things "
        "rather than only describing them. When they ask how to do something:",
        tour_rung,
        "2. otherwise take a page_snapshot and walk them through the real screen with "
        "show_steps, referencing elements by their [index];",
        "3. answer in words only when neither fits, or when the question is about facts "
        "rather than doing something (then search_knowledge and cite).",
    ]
    if plan.client & page_tools.MUTATING_TOOLS:
        ladder.append(
            "You may also do it FOR them with page_act / page_navigate — but only when they "
            'clearly asked you to ("do it for me", "go ahead"), never on your own initiative, '
            "and never for anything destructive, irreversible, or involving payment. Say what "
            "you are about to do, do it, then confirm what happened by reading the page."
        )
    else:
        ladder.append(
            "You can look at the page and point at things, but you cannot click or type for "
            "them. If they ask you to do it, explain that you can show them where instead."
        )
    ladder.append(
        "Never type into a password field and never ask for a password. If a step needs "
        "credentials or a payment, stop and hand that step to the person."
    )
    parts.append("\n".join(ladder))
    return parts


def _page_context_line(conversation: Conversation | None) -> str | None:
    """Tell the model which screen the person is on, so it can answer "here"."""
    if conversation is None or not isinstance(conversation.attributes, dict):
        return None
    url = conversation.attributes.get("page_url")
    title = conversation.attributes.get("page_title")
    if not isinstance(url, str) or not url:
        return None
    if isinstance(title, str) and title:
        return f'The person is currently on the page "{title}" ({url}).'
    return f"The person is currently on {url}."


async def build_history(
    session: AsyncSession, conversation: Conversation | None
) -> list[ChatMessage]:
    if conversation is None:
        return []
    rows = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id, Message.visibility == "public")
                .order_by(Message.created_at, Message.id)
            )
        )
        .scalars()
        .all()
    )
    history: list[ChatMessage] = []
    for message in rows:
        if message.author_type == "contact":
            history.append(ChatMessage.user(message.content))
        else:
            history.append(ChatMessage.assistant(message.content))
    return history[-HISTORY_CAP:]


async def _trigger_text(
    session: AsyncSession, run: AgentRun, conversation: Conversation | None
) -> str | None:
    """The visitor message this run is answering (falls back to the latest one)."""
    if run.trigger_message_id:
        message = await session.get(Message, run.trigger_message_id)
        if message is not None and message.content:
            return message.content
    if conversation is None:
        return None
    return (
        await session.execute(
            select(Message.content)
            .where(
                Message.conversation_id == conversation.id,
                Message.direction == "in",
                Message.visibility == "public",
                Message.author_type == "contact",
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _reply_language(
    conversation: Conversation | None, trigger_text: str | None
) -> tuple[str | None, str]:
    """(locale, source) for the reply — per-message detection beats history.

    The W13 rule: answer in the language of the LATEST visitor message. Only
    when the message is too short to detect ("ok", "danke") does the stored
    conversation locale act as a tiebreak.
    """
    detected = detect_language(trigger_text)
    if detected is not None:
        return detected, "detected"
    return _conversation_locale(conversation), "stored"


async def _tour_autostart_policy(session: AsyncSession, conversation: Conversation | None) -> str:
    """`tour_autostart_policy` from the widget inbox settings JSON (default "ask")."""
    if conversation is None or not conversation.inbox_id:
        return TOUR_POLICY_AUTO  # sandbox / detached runs keep legacy behavior
    inbox = await session.get(Inbox, conversation.inbox_id)
    raw = (inbox.config or {}).get("tour_autostart_policy") if inbox is not None else None
    return raw if isinstance(raw, str) and raw in _TOUR_POLICIES else TOUR_POLICY_ASK


def _strip_show_guide(plan: tool_registry.ToolPlan) -> None:
    """Withhold `show_guide` entirely (`tour_autostart_policy: never`) — a tool
    that is not offered cannot be called, which beats refusing the call."""
    plan.specs = [spec for spec in plan.specs if spec.name != page_tools.SHOW_GUIDE]
    plan.client.discard(page_tools.SHOW_GUIDE)
    plan.policy.pop(page_tools.SHOW_GUIDE, None)
    plan.control.pop(page_tools.SHOW_GUIDE, None)


def apply_citations(content: str, citations: list[dict]) -> tuple[str, list[dict]]:
    """Keep in-range [n] markers, strip the rest, and return the cited subset."""
    max_n = len(citations)
    used: set[int] = set()

    def repl(match: re.Match[str]) -> str:
        n = int(match.group(1))
        if 1 <= n <= max_n:
            used.add(n)
            return match.group(0)
        return ""

    cleaned = _CITATION_RE.sub(repl, content)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    referenced = [c for c in citations if c.get("n") in used]
    return cleaned, referenced


def _run_citations(run: AgentRun) -> list[dict]:
    return list(run.citations or [])


def _max_tool_calls(agent: Agent) -> int:
    settings = agent.settings if isinstance(agent.settings, dict) else {}
    guardrails = settings.get("guardrails") or {}
    try:
        return max(1, int(guardrails.get("max_tool_calls") or 8))
    except (TypeError, ValueError):
        return 8


async def _max_ord(session: AsyncSession, run_id: str) -> int:
    value = (
        await session.execute(select(func.max(AgentStep.ord)).where(AgentStep.run_id == run_id))
    ).scalar_one_or_none()
    return int(value or 0)


async def _executed_tool_calls(session: AsyncSession, run_id: str) -> int:
    value = (
        await session.execute(
            select(func.count())
            .select_from(AgentStep)
            .where(AgentStep.run_id == run_id, AgentStep.kind == "tool_call")
        )
    ).scalar_one()
    return int(value)


async def _member_name(session: AsyncSession, user_id: str | None) -> str | None:
    if not user_id:
        return None
    from app.models.user import User

    user = await session.get(User, user_id)
    return user.name if user is not None else None


# ---------------------------------------------------------------------------
# core execution
# ---------------------------------------------------------------------------


async def execute_run(
    session: AsyncSession,
    run: AgentRun,
    *,
    mode: str = "live",
    conversation: Conversation | None = None,
    initial_messages: list[ChatMessage] | None = None,
) -> ExecutionResult:
    """Run (or resume) one agent execution. ``mode='sandbox'`` never persists."""
    sandbox = mode == "sandbox"
    agent = await session.get(Agent, run.agent_id)
    if agent is None:
        run.status = "failed"
        run.error = "agent not found"
        run.finished_at = utcnow()
        return ExecutionResult("failed", None, [], _run_citations(run))

    if conversation is None and not sandbox:
        conversation = await session.get(Conversation, run.conversation_id)
    actor = Actor(type="agent", id=agent.id, label=agent.name)

    if not sandbox:
        if run.status == "queued":
            # 1 inbound → 1 reply, in order: a follow-up run created while an
            # earlier run was still in flight stays queued until that run
            # reaches a terminal status (`_complete` chains it; the reaper is
            # the backstop). Two runs answering one thread at once is how a
            # visitor gets a reply to the wrong message.
            if await _earlier_active_run(session, run) is not None:
                return ExecutionResult(run.status, None, [], _run_citations(run))
            # A human (or resolution) took the thread out of the AI queue while
            # this follow-up waited — the message is theirs to answer now.
            if (
                run.trigger_message_id
                and conversation is not None
                and conversation.status != "pending"
            ):
                run.status = "canceled"
                run.error = "conversation left the AI queue before this run started"
                run.finished_at = utcnow()
                await session.flush()
                return ExecutionResult("canceled", None, [], _run_citations(run))
        claim_result = _claim(run)
        if claim_result == "skip":
            return ExecutionResult(run.status, None, [], _run_citations(run))
        if claim_result == "crashed":
            ctx = _context(session, run, agent, conversation, actor, mode, {})
            sink = _StepSink(session, run, mode, await _max_ord(session, run.id) + 1)
            return await _fail(ctx, sink, "worker lease expired mid-run")
        await session.flush()

    workspace = await session.get(Workspace, run.workspace_id)
    workspace_name = workspace.name if workspace is not None else "our team"

    plan = await tool_registry.resolve_agent_tools(
        session, run.workspace_id, agent, conversation=conversation
    )
    trigger_text = None if sandbox else await _trigger_text(session, run, conversation)
    tour_policy = (
        TOUR_POLICY_AUTO if sandbox else await _tour_autostart_policy(session, conversation)
    )
    tour_imperative = guides.is_tour_imperative(trigger_text)
    if tour_policy == TOUR_POLICY_NEVER:
        _strip_show_guide(plan)
    ctx = _context(
        session,
        run,
        agent,
        conversation,
        actor,
        mode,
        plan.action_ids,
        tour_policy=tour_policy,
        tour_imperative=tour_imperative,
    )
    start_ord = (0 if sandbox else await _max_ord(session, run.id)) + 1
    sink = _StepSink(session, run, mode, start_ord)

    # --- restore (resume) or build the provider message list ---
    if run.pending_tool_call:
        messages = deserialize_messages(run.messages_snapshot)
        if run.pending_tool_call.get("client_op_id"):
            await _apply_client_result(ctx, sink, messages)
            run.pending_tool_call = None
            run.messages_snapshot = None
        else:
            resume_outcome = await _apply_pending_decision(ctx, sink, messages)
            run.pending_tool_call = None
            run.messages_snapshot = None
            if resume_outcome is not None and resume_outcome.control in ("handoff", "close"):
                return await _finalize_control(ctx, sink, resume_outcome)
    elif initial_messages is not None:
        messages = list(initial_messages)
    else:
        reply_locale, reply_locale_source = _reply_language(conversation, trigger_text)
        if reply_locale is None and conversation is not None and conversation.contact_id:
            # Last resort before the language vacuum: the contact's stored
            # locale (stamped from the browser at widget boot, overwritten by
            # what they actually write). Without it, an undetectable first
            # message leaves the model to guess — typically in the language
            # the agent's persona happens to be written in.
            contact = await session.get(Contact, conversation.contact_id)
            if contact is not None and contact.locale:
                reply_locale = normalize_locale(contact.locale)
                reply_locale_source = "stored"
        messages = [
            ChatMessage.system(
                compose_system_prompt(
                    agent,
                    workspace_name,
                    conversation=conversation,
                    plan=plan,
                    reply_locale=reply_locale,
                    reply_locale_source=reply_locale_source,
                    tour_policy=tour_policy,
                    tour_imperative=tour_imperative,
                )
            )
        ]
        messages.extend(await build_history(session, conversation))

    # --- main loop ---
    max_calls = _max_tool_calls(agent)
    tool_calls_used = 0 if sandbox else await _executed_tool_calls(session, run.id)
    await _set_agent_typing(ctx, True)
    safety = 0
    while True:
        safety += 1
        if safety > max_calls + 5:  # absolute backstop; guardrail below is the real limit
            return await _fallback(
                ctx, sink, reason="loop safety limit reached", status="handed_off", guardrail=True
            )

        try:
            provider, model_key = await resolve_chat(session, run.workspace_id, agent.model_ref)
            request = ChatRequest(
                model=model_key,
                messages=messages,
                tools=plan.specs,
                temperature=agent.temperature,
            )
            started = time.perf_counter()
            result = await provider.generate(request)
            latency_ms = int((time.perf_counter() - started) * 1000)
        except ProviderError as exc:
            if exc.retryable:
                raise
            return await _fail(ctx, sink, str(exc))
        except Exception as exc:  # noqa: BLE001 — never strand a conversation on a bad provider
            return await _fail(ctx, sink, f"{type(exc).__name__}: {exc}")

        run.input_tokens += result.usage.input_tokens
        run.output_tokens += result.usage.output_tokens
        await sink.add(
            "llm_call",
            name=model_key,
            input={"num_messages": len(messages), "num_tools": len(plan.specs)},
            output={
                "content": result.content,
                "tool_calls": [{"name": tc.name, "input": tc.input} for tc in result.tool_calls],
                "finish_reason": result.finish_reason,
            },
            latency_ms=latency_ms,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )

        if not result.tool_calls:
            content = (result.content or "").strip()
            if not content:
                return await _fallback(ctx, sink, reason="empty reply", status="handed_off")
            return await _final_reply(ctx, sink, content)

        if tool_calls_used >= max_calls:
            return await _fallback(
                ctx,
                sink,
                reason=f"reached max_tool_calls={max_calls}",
                status="handed_off",
                guardrail=True,
            )

        # Record the assistant tool-call message; execute only the first call this step.
        messages.append(ChatMessage.assistant(result.content, result.tool_calls))
        for extra in result.tool_calls[1:]:
            messages.append(
                ChatMessage.tool_result(
                    extra.id,
                    json.dumps({"error": "one tool at a time — call again"}),
                    is_error=True,
                )
            )
        call = result.tool_calls[0]
        tool_calls_used += 1
        policy = plan.policy.get(call.name, DEFAULT_POLICIES.get(call.name, ACTION_DEFAULT_POLICY))

        if policy == POLICY_DISABLED:
            await sink.add(
                "tool_result", name=call.name, input=call.input, output={"error": "tool disabled"}
            )
            messages.append(
                ChatMessage.tool_result(
                    call.id, json.dumps({"error": "tool disabled"}), is_error=True
                )
            )
            continue

        if policy == POLICY_REQUIRE_APPROVAL and not sandbox:
            return await _pause_for_approval(ctx, sink, messages, call)

        if call.name in plan.client:
            if (
                call.name == page_tools.SHOW_GUIDE
                and ctx.tour_policy == TOUR_POLICY_ASK
                and not ctx.tour_imperative
                and not sandbox
            ):
                # Policy "ask" on an informational question: never hijack the
                # screen. The call becomes a one-tap offer card attached to the
                # final reply; the model is told to answer in words.
                await _offer_instead_of_starting(ctx, sink, messages, call)
                continue
            client_error = await _reject_client_call(ctx, sink, messages, call, plan)
            if client_error is not None:
                continue
            if sandbox:
                # A dry run has no browser on the other end; report what WOULD
                # have been asked of the page so the trace still reads honestly.
                await sink.add("tool_call", name=call.name, input=call.input)
                dry = {"dry_run": True, **_client_op(plan, call)}
                await sink.add("tool_result", name=call.name, output=dry)
                messages.append(ChatMessage.tool_result(call.id, json.dumps(dry)))
                continue
            return await _pause_for_client(ctx, sink, messages, call, plan)

        await sink.add("tool_call", name=call.name, input=call.input)
        outcome = await tool_registry.execute_tool(ctx, call.name, call.input)
        await sink.add("tool_result", name=call.name, output=outcome.result)
        messages.append(
            ChatMessage.tool_result(call.id, json.dumps(outcome.result), is_error=outcome.is_error)
        )
        if outcome.reply_message_id and not sandbox:
            run.reply_message_id = outcome.reply_message_id
        if outcome.control in ("handoff", "close"):
            return await _finalize_control(ctx, sink, outcome)


async def run_sandbox(
    session: AsyncSession,
    agent: Agent,
    *,
    message: str,
    history: list[tuple[str, str]] | None = None,
) -> ExecutionResult:
    """Run the SAME loop against an ephemeral, non-persisted context (dry-run).

    The sandbox conversation carries page-control consent so a dry run exercises
    the in-app tools too: they answer `{"dry_run": true, …}` instead of reaching a
    browser, which is what makes the guidance prompt testable without a visitor.
    """
    workspace = await session.get(Workspace, agent.workspace_id)
    workspace_name = workspace.name if workspace is not None else "our team"
    conversation = Conversation(
        id=uuid7(),
        workspace_id=agent.workspace_id,
        status="pending",
        attributes={"page_control_consent": True},
    )
    run = AgentRun(
        id=uuid7(),
        workspace_id=agent.workspace_id,
        conversation_id=conversation.id,
        agent_id=agent.id,
        status="running",
        input_tokens=0,
        output_tokens=0,
        citations=[],
    )
    sandbox_plan = await tool_registry.resolve_agent_tools(
        session, agent.workspace_id, agent, conversation=conversation
    )
    messages: list[ChatMessage] = [
        ChatMessage.system(
            compose_system_prompt(
                agent,
                workspace_name,
                conversation=conversation,
                plan=sandbox_plan,
                reply_locale=_conversation_locale(conversation),
            )
        )
    ]
    for role, content in history or []:
        if role in ("user", "contact"):
            messages.append(ChatMessage.user(content))
        else:
            messages.append(ChatMessage.assistant(content))
    messages.append(ChatMessage.user(message))
    return await execute_run(
        session, run, mode="sandbox", conversation=conversation, initial_messages=messages
    )


def _context(
    session: AsyncSession,
    run: AgentRun,
    agent: Agent,
    conversation: Conversation | None,
    actor: Actor,
    mode: str,
    action_ids: dict[str, str],
    *,
    tour_policy: str = TOUR_POLICY_AUTO,
    tour_imperative: bool = False,
) -> ToolContext:
    return ToolContext(
        session=session,
        workspace_id=run.workspace_id,
        run=run,
        agent=agent,
        conversation=conversation,
        actor=actor,
        mode=mode,
        action_ids=action_ids,
        tour_policy=tour_policy,
        tour_imperative=tour_imperative,
    )


async def _earlier_active_run(session: AsyncSession, run: AgentRun) -> str | None:
    """Id of an older non-terminal run on the same conversation, if any."""
    rows = (
        await session.execute(
            select(AgentRun.id, AgentRun.created_at).where(
                AgentRun.conversation_id == run.conversation_id,
                AgentRun.id != run.id,
                AgentRun.status.in_(_ACTIVE_STATUSES),
            )
        )
    ).all()
    for other_id, created_at in rows:
        if (created_at, other_id) < (run.created_at, run.id):
            return other_id
    return None


def _claim(run: AgentRun) -> str:
    """Claim a run for this worker. Returns 'ok' | 'skip' | 'crashed'."""
    now = utcnow()
    if run.status in _TERMINAL_STATUSES:
        return "skip"
    if run.status == "running":
        if run.lease_expires_at is not None and run.lease_expires_at > now:
            return "skip"  # another worker holds a live lease
        if not run.pending_tool_call:
            return "crashed"  # lease expired mid-run, nothing to resume from (v1: fail)
    if run.started_at is None:
        run.started_at = now
    run.status = "running"
    run.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    return "ok"


async def _apply_pending_decision(
    ctx: ToolContext, sink: _StepSink, messages: list[ChatMessage]
) -> ToolOutcome | None:
    """Resume preamble: execute the approved tool, or append the denial as a tool result."""
    pending = ctx.run.pending_tool_call or {}
    call_id = pending.get("id", "")
    name = pending.get("name", "")
    tool_input = pending.get("input") or {}
    approval = await ctx.session.get(ApprovalRequest, pending.get("approval_request_id"))

    if approval is not None and approval.status == "pending":
        # The decision has not committed yet (enqueue-before-commit) — retry.
        raise _ResumeNotReady()

    if approval is not None and approval.status == "approved":
        await sink.add("tool_call", name=name, input=tool_input)
        outcome = await tool_registry.execute_tool(ctx, name, tool_input)
        await sink.add("tool_result", name=name, output=outcome.result)
        messages.append(
            ChatMessage.tool_result(call_id, json.dumps(outcome.result), is_error=outcome.is_error)
        )
        if outcome.reply_message_id and not ctx.sandbox:
            ctx.run.reply_message_id = outcome.reply_message_id
        return outcome

    # rejected or expired → denial-as-tool-result (the model sees it and adapts)
    if approval is not None and approval.status == "expired":
        denial = "The approval request expired without a decision; do not perform the action."
    else:
        who = (
            await _member_name(ctx.session, approval.decided_by if approval else None)
            or "a teammate"
        )
        note = approval.note if approval is not None else None
        denial = f"Rejected by {who}: {note}" if note else f"Rejected by {who}."
    await sink.add("tool_result", name=name, output={"error": denial})
    messages.append(ChatMessage.tool_result(call_id, json.dumps({"error": denial}), is_error=True))
    return None


async def _pause_for_approval(
    ctx: ToolContext, sink: _StepSink, messages: list[ChatMessage], call: ToolCall
) -> ExecutionResult:
    now = utcnow()
    approval = ApprovalRequest(
        workspace_id=ctx.run.workspace_id,
        run_id=ctx.run.id,
        conversation_id=ctx.run.conversation_id,
        agent_id=ctx.agent.id,
        tool_key=call.name,
        tool_input=call.input,
        status="pending",
        requested_at=now,
        expires_at=now + timedelta(hours=APPROVAL_TTL_HOURS),
    )
    ctx.session.add(approval)
    await ctx.session.flush()

    ctx.run.pending_tool_call = {
        "id": call.id,
        "name": call.name,
        "input": call.input,
        "approval_request_id": approval.id,
    }
    ctx.run.messages_snapshot = serialize_messages(messages)
    ctx.run.status = "awaiting_approval"
    ctx.run.lease_expires_at = None
    await sink.add(
        "approval_request", name=call.name, input=call.input, output={"approval_id": approval.id}
    )
    await ctx.session.flush()

    await _notify_approvers(ctx.session, ctx.run.workspace_id, approval, ctx.agent)
    await broadcast(
        workspace_topic(ctx.run.workspace_id),
        "approval.pending",
        {
            "approval_id": approval.id,
            "conversation_id": ctx.run.conversation_id,
            "agent_name": ctx.agent.name,
            "tool_key": call.name,
            "tool_input": call.input,
        },
    )
    await emit(
        ctx.session,
        Event(
            name=EventNames.APPROVAL_REQUESTED,
            workspace_id=ctx.run.workspace_id,
            payload={
                "approval_id": approval.id,
                "run_id": ctx.run.id,
                "conversation_id": ctx.run.conversation_id,
                "tool_key": call.name,
            },
            actor=ctx.actor,
        ),
    )
    await _set_agent_typing(ctx, False)  # parked for a human decision — stop the dots
    await _publish_run_status(ctx)
    return ExecutionResult("awaiting_approval", None, sink.sandbox_steps, _run_citations(ctx.run))


async def _reject_client_call(
    ctx: ToolContext,
    sink: _StepSink,
    messages: list[ChatMessage],
    call: ToolCall,
    plan: tool_registry.ToolPlan,
) -> str | None:
    """Answer a malformed or over-budget client call locally; None means "send it".

    Two guards, both cheaper than a browser round-trip: schema validation, and a
    per-run cap on ops that change the visitor's app. The cap counts what already
    happened in this run's trace, so it survives the pause/resume cycle that every
    client op goes through.
    """
    action_def = plan.client_action_defs.get(call.name)
    if action_def is not None:
        error = client_actions.validate(action_def, call.input)
        if error is None:
            used = await _app_actions_used(ctx.session, ctx.run.id)
            if used >= client_actions.MAX_ACTION_CALLS:
                error = (
                    f"you have already run {used} app actions in one go — "
                    "stop and tell the person what happened and what is left"
                )
    else:
        error = page_tools.validate(call.name, call.input)
        if error is None and call.name in page_tools.MUTATING_TOOLS:
            used = await _client_ops_used(ctx.session, ctx.run.id)
            if used >= page_tools.MAX_MUTATING_OPS:
                error = (
                    f"you have already changed this page {used} times in one go — "
                    "stop and tell the person what you did and what is left"
                )
    if error is None:
        return None
    await sink.add("tool_call", name=call.name, input=call.input)
    await sink.add("tool_result", name=call.name, output={"error": error})
    messages.append(ChatMessage.tool_result(call.id, json.dumps({"error": error}), is_error=True))
    return error


async def _client_ops_used(session: AsyncSession, run_id: str) -> int:
    """Mutating page ops already performed in this run (counted from the trace)."""
    rows = (
        (
            await session.execute(
                select(AgentStep.name).where(
                    AgentStep.run_id == run_id,
                    AgentStep.kind == "client_request",
                )
            )
        )
        .scalars()
        .all()
    )
    return sum(1 for name in rows if name in page_tools.MUTATING_TOOLS)


async def _app_actions_used(session: AsyncSession, run_id: str) -> int:
    """Client actions already dispatched in this run (counted from the trace)."""
    rows = (
        (
            await session.execute(
                select(AgentStep.name).where(
                    AgentStep.run_id == run_id,
                    AgentStep.kind == "client_request",
                )
            )
        )
        .scalars()
        .all()
    )
    return sum(1 for name in rows if name is not None and client_actions.is_action_name(name))


async def _pause_for_client(
    ctx: ToolContext,
    sink: _StepSink,
    messages: list[ChatMessage],
    call: ToolCall,
    plan: tool_registry.ToolPlan,
) -> ExecutionResult:
    """Hand a client op to the visitor's widget and park the run until it answers."""
    op = _client_op(plan, call)
    op_id = uuid7()
    ctx.run.pending_tool_call = {
        "id": call.id,
        "name": call.name,
        "input": call.input,
        "client_op_id": op_id,
        "op": op["op"],
    }
    ctx.run.messages_snapshot = serialize_messages(messages)
    ctx.run.status = "awaiting_client"
    ctx.run.lease_expires_at = None
    await sink.add(
        "client_request", name=call.name, input=call.input, output={"op_id": op_id, **op}
    )
    if call.name == page_tools.SHOW_GUIDE:
        await _stamp_tour_start(ctx, call, op_id)
    await ctx.session.flush()
    await _publish_run_status(ctx)

    # AFTER COMMIT, not now. A flush is invisible to other sessions, and the widget
    # is fast: it would execute the op and POST the result while this transaction
    # was still open, hit a run row that still says "running", and have its result
    # rejected as stale — the guide would then stall until the timeout sweep.
    _broadcast_after_commit(
        ctx.session,
        conversation_topic(ctx.run.conversation_id),
        "copilot.op",
        {
            "conversation_id": ctx.run.conversation_id,
            "run_id": ctx.run.id,
            "op_id": op_id,
            "tool": call.name,
            **op,
        },
    )
    await _set_agent_typing(ctx, False)  # PageAssist UI takes over — stop the dots
    return ExecutionResult("awaiting_client", None, sink.sandbox_steps, _run_citations(ctx.run))


def _client_op(plan: tool_registry.ToolPlan, call: ToolCall) -> dict[str, Any]:
    """The wire op for one deferred client call — page op or registered action."""
    action_def = plan.client_action_defs.get(call.name)
    if action_def is not None:
        return client_actions.op_for(action_def, call.input)
    return page_tools.op_for(call.name, call.input)


def _transcript_locale(conversation: Conversation | None) -> str:
    return _conversation_locale(conversation) or DEFAULT_LOCALE


async def _stamp_tour_start(ctx: ToolContext, call: ToolCall, op_id: str) -> None:
    """Link the parked `show_guide` op to its conversation + announce the start.

    The link (`conversation.attributes["tour_run"]`) is what lets the tour
    telemetry channel — the reliable one — resume this run and keep the
    transcript truthful (`app.agents.tour_events`). The announce line is the
    visitor-visible "Starting tour …" so an agent-initiated takeover of the
    screen is never unexplained.
    """
    if ctx.sandbox or ctx.conversation is None:
        return
    tour_id = str(call.input.get("tour_id") or "")
    tour = await guides.live_tour(ctx.session, ctx.workspace_id, tour_id) if tour_id else None
    if tour is None:
        return  # the widget will answer with a load error; nothing to link
    ctx.conversation.attributes = {
        **ctx.conversation.attributes,
        tour_events.TOUR_RUN_ATTR: {
            "tour_id": tour.id,
            "run_id": ctx.run.id,
            "op_id": op_id,
            "name": tour.name,
            "announced": True,
            "started_at": utcnow().isoformat(),
        },
    }
    await conversations_service.add_message(
        ctx.session,
        ctx.conversation,
        direction="out",
        author_type="system",
        author_id=None,
        author_name="",
        content=translate(
            "tour.transcript.starting", _transcript_locale(ctx.conversation), name=tour.name
        ),
        visibility="public",
        meta={"kind": "tour_event", "event": "starting", "tour_id": tour.id},
        actor=ctx.actor,
        deliver=False,
    )


async def _offer_instead_of_starting(
    ctx: ToolContext, sink: _StepSink, messages: list[ChatMessage], call: ToolCall
) -> None:
    """Turn a `show_guide` call into a deferred offer card (policy "ask").

    The model keeps its mental model ("I recommended that tour") while the
    person keeps their screen: the offer is attached to the final reply as a
    `{"kind": "tour_offer"}` attachment the messenger renders as a card whose
    button calls the existing start mechanism.
    """
    tour_id = str(call.input.get("tour_id") or "")
    tour = await guides.live_tour(ctx.session, ctx.workspace_id, tour_id) if tour_id else None
    await sink.add("tool_call", name=call.name, input=call.input)
    if tour is None:
        result: dict[str, Any] = {"error": "unknown or unpublished tour_id — call find_guide first"}
        await sink.add("tool_result", name=call.name, output=result)
        messages.append(ChatMessage.tool_result(call.id, json.dumps(result), is_error=True))
        return
    offer = tour_events.offer_payload(tour_id=tour.id, title=tour.name, steps=len(tour.steps or []))
    if ctx.conversation is not None:
        ctx.conversation.attributes = {**ctx.conversation.attributes, TOUR_OFFER_ATTR: offer}
    result = {
        "ok": True,
        "offer_attached": True,
        "tour": tour.name,
        "note": (
            "This workspace asks before playing tours: the tour did NOT start. A one-tap "
            f"offer card for “{tour.name}” will be attached to your reply. Answer the "
            "person's question in words and mention they can start the tour from the card. "
            "Never say the tour is playing."
        ),
    }
    await sink.add("tool_result", name=call.name, output=result)
    messages.append(ChatMessage.tool_result(call.id, json.dumps(result)))


def _take_tour_offer(ctx: ToolContext) -> dict[str, Any] | None:
    """Pop the pending offer (explicit `show_guide` first, else the best
    `find_guide` hit) — cleared unconditionally so no stale card ever attaches
    to a later, unrelated reply."""
    if ctx.conversation is None or not isinstance(ctx.conversation.attributes, dict):
        return None
    attributes = dict(ctx.conversation.attributes)
    explicit = attributes.pop(TOUR_OFFER_ATTR, None)
    candidate = attributes.pop(TOUR_OFFER_CANDIDATE_ATTR, None)
    if TOUR_OFFER_ATTR in ctx.conversation.attributes or (
        TOUR_OFFER_CANDIDATE_ATTR in ctx.conversation.attributes
    ):
        ctx.conversation.attributes = attributes
    if ctx.tour_policy != TOUR_POLICY_ASK or ctx.tour_imperative:
        return None
    offer = explicit or candidate
    return offer if isinstance(offer, dict) and offer.get("tour_id") else None


async def _publish_run_status(ctx: ToolContext, *, terminal: bool = False) -> None:
    """Tell the widget (conversation topic) where this run stands.

    Guaranteed on every terminal path — success, tool error, exception, timeout
    — so a "working on the page…" indicator can never outlive its run. The
    dashboards' workspace-topic broadcast is separate and unchanged.
    """
    if ctx.sandbox or ctx.conversation is None:
        return
    await broadcast(
        conversation_topic(ctx.conversation.id),
        "agent_run.updated",
        {
            "run_id": ctx.run.id,
            "conversation_id": ctx.conversation.id,
            "status": ctx.run.status,
            "terminal": terminal,
        },
    )


async def resolve_guide_wait(
    session: AsyncSession, run: AgentRun, *, event: str, tour_id: str
) -> str | None:
    """Settle a run parked on `show_guide` from REAL playback telemetry.

    This is the root-cause fix for the false-failure loop: the messenger
    iframe's `/copilot/result` round trip is lossy (dropped when the thread
    screen is closed, and its in-memory op map dies on every tour-step
    navigation), so the widget "acked on a channel nobody awaits" and the
    sweep then reported a timeout for a tour that was playing fine. Playback
    telemetry does not go through the iframe and cannot lie about lifecycle:

    - `started`   → inject a success tool-result and resume the model, which
                    can now truthfully tell the person the tour is running;
    - `dismissed` → finalize the run silently: the person chose to stop, and
                    any generated reply here becomes an apology for a problem
                    that does not exist;
    - `completed` → finalize silently too — the congrats comes from
                    `tour_events` in one voice, not from a resumed loop AND a
                    mirror at the same time.

    Returns "resumed" | "finalized" | None (not parked on this tour's guide op).
    """
    pending = run.pending_tool_call or {}
    if (
        run.status != "awaiting_client"
        or pending.get("op") != "guide"
        or "result" in pending
        or str((pending.get("input") or {}).get("tour_id") or "") != tour_id
    ):
        return None
    if event == "started":
        run.pending_tool_call = {
            **pending,
            "result": {
                "ok": True,
                "note": (
                    "confirmed: the tour is now playing on the page. Tell the person in "
                    "one short line what it walks them through — do not ask them to reload."
                ),
            },
        }
        await session.flush()
        await enqueue("execute_agent_run", run_id=run.id)
        return "resumed"
    if event not in ("completed", "dismissed"):
        return None
    agent = await session.get(Agent, run.agent_id)
    conversation = await session.get(Conversation, run.conversation_id)
    if agent is None:
        return None
    ctx = _context(
        session,
        run,
        agent,
        conversation,
        Actor(type="agent", id=agent.id, label=agent.name),
        "live",
        {},
    )
    sink = _StepSink(session, run, "live", await _max_ord(session, run.id) + 1)
    await sink.add(
        "tool_result",
        name=pending.get("name"),
        output={"ok": True, "outcome": event, "source": "tour_telemetry"},
    )
    run.pending_tool_call = None
    run.messages_snapshot = None
    await _complete(ctx, sink, "completed")
    return "finalized"


def _broadcast_after_commit(
    session: AsyncSession, topic: str, event: str, payload: dict[str, Any]
) -> None:
    """Publish `payload` once this session's transaction commits.

    Registered as a one-shot `after_commit` listener on the underlying sync
    session. If the transaction rolls back the listener never fires, which is
    exactly right: an op nobody can see was never asked for.
    """

    fired = False

    def _on_commit(_session: object) -> None:
        # One-shot by flag, not by `event.remove`: removing a listener from inside
        # its own dispatch is not supported, and the session is reused for the rest
        # of the request/task anyway.
        nonlocal fired
        if fired:
            return
        fired = True
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(broadcast(topic, event, payload))

    event_.listen(session.sync_session, "after_commit", _on_commit)


async def submit_client_result(
    session: AsyncSession, run: AgentRun, *, op_id: str, result: dict[str, Any]
) -> None:
    """Record the widget's answer to a deferred page op and resume the run.

    Raises `ConflictError` when the run is not waiting, or when the op id does not
    match the parked call — a stale widget (reconnected after a reload, replaying
    an old op) must not be able to inject a result into a later step.
    """
    pending = run.pending_tool_call or {}
    if run.status != "awaiting_client" or not pending.get("client_op_id"):
        raise ConflictError("this run is not waiting for the page")
    if pending.get("client_op_id") != op_id:
        raise ConflictError("that page result is for a different step")
    run.pending_tool_call = {**pending, "result": result}
    await session.flush()
    await enqueue("execute_agent_run", run_id=run.id)


async def _apply_client_result(
    ctx: ToolContext, sink: _StepSink, messages: list[ChatMessage]
) -> None:
    """Resume preamble for a page op: append the widget's result as the tool result.

    A parked run with NO ``result`` key is not a failure — it is this worker
    arriving before the widget answered (the op is broadcast from inside the
    transaction that parks it, so a fast browser can round-trip before the commit
    lands, and a duplicate queue delivery can re-enter here). Raising
    ``_ResumeNotReady`` hands it back to the queue's backoff, the same way the
    approval path handles an uncommitted decision. Genuine silence is handled by
    `sweep_stale_client_waits`, which WRITES a timeout result — so a missing key
    always means "too early", never "gave up".
    """
    pending = ctx.run.pending_tool_call or {}
    if "result" not in pending:
        raise _ResumeNotReady()
    call_id = pending.get("id", "")
    name = pending.get("name", "")
    result = pending.get("result")
    if not isinstance(result, dict):
        result = {"error": "the page returned an unreadable result"}
    is_error = bool(result.get("error")) or result.get("ok") is False
    await sink.add("tool_result", name=name, output=result)
    messages.append(ChatMessage.tool_result(call_id, json.dumps(result), is_error=is_error))


async def sweep_stale_client_waits(session: AsyncSession) -> list[AgentRun]:
    """Resume runs whose page op went unanswered, so none is stranded.

    The run comes back with an error tool-result rather than being killed: the
    model gets to say "I lost the page — here is what to do yourself", which is a
    far better outcome for the person than silence.
    """
    cutoff = utcnow() - timedelta(seconds=CLIENT_OP_TIMEOUT_SECONDS)
    stale = (
        (
            await session.execute(
                select(AgentRun).where(
                    AgentRun.status == "awaiting_client",
                    AgentRun.updated_at <= cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    action_cutoff = utcnow() - timedelta(seconds=ACTION_CONFIRM_TIMEOUT_SECONDS)
    swept: list[AgentRun] = []
    for run in stale:
        pending = run.pending_tool_call or {}
        if "result" in pending:
            continue  # a result landed; the queued resume will pick it up
        if pending.get("op") == "action" and run.updated_at > action_cutoff:
            continue  # likely a confirm card in front of a person — give them time
        if pending.get("op") == "action":
            message = "the person did not confirm the action in time"
        elif pending.get("op") == "guide":
            # A tour whose `started` telemetry never arrived. That is genuinely
            # unconfirmed — but "reload the page" advice for a tour that may be
            # playing right now is exactly the false-failure loop. Be honest
            # about the uncertainty instead.
            message = (
                "no confirmation arrived that the tour started. It may be playing anyway. "
                "Do NOT apologize or tell the person to reload — ask them to say so if the "
                "tour did not appear, and offer to explain the steps in words instead."
            )
        else:
            message = "timed out waiting for the page"
        run.pending_tool_call = {**pending, "result": {"error": message}}
        await enqueue("execute_agent_run", run_id=run.id)
        swept.append(run)
    if swept:
        await session.flush()
    return swept


async def _final_reply(ctx: ToolContext, sink: _StepSink, content: str) -> ExecutionResult:
    cleaned, referenced = apply_citations(content, _run_citations(ctx.run))
    if not ctx.sandbox and ctx.conversation is not None:
        offer = _take_tour_offer(ctx)
        meta: dict[str, Any] = {"agent_run_id": ctx.run.id, "citations": referenced}
        if ctx.run.trigger_message_id:
            # Which inbound message this reply addresses — the audit trail for
            # the 1-inbound-→-1-reply guarantee.
            meta["reply_to"] = ctx.run.trigger_message_id
        message = await conversations_service.add_message(
            ctx.session,
            ctx.conversation,
            direction="out",
            author_type="agent",
            author_id=ctx.agent.id,
            author_name=ctx.agent.name,
            content=cleaned,
            attachments=[offer] if offer else None,
            actor=ctx.actor,
            meta=meta,
        )
        ctx.run.reply_message_id = message.id
    await sink.add(
        "final_reply",
        output={
            "content": cleaned,
            "citations": referenced,
            "message_id": ctx.run.reply_message_id,
        },
    )
    return await _complete(ctx, sink, "completed", reply=cleaned)


async def _finalize_control(
    ctx: ToolContext, sink: _StepSink, outcome: ToolOutcome
) -> ExecutionResult:
    status = "handed_off" if outcome.control == "handoff" else "completed"
    await sink.add(
        "handoff" if outcome.control == "handoff" else "final_reply", output=outcome.result
    )
    return await _complete(ctx, sink, status)


async def _fallback(
    ctx: ToolContext,
    sink: _StepSink,
    *,
    reason: str,
    status: str,
    guardrail: bool = False,
) -> ExecutionResult:
    if guardrail:
        await sink.add("guardrail", output={"reason": reason})
    if not ctx.sandbox and ctx.conversation is not None:
        if ctx.conversation.status == "pending":
            await conversations_service.update_status(
                ctx.session, ctx.conversation, "open", actor=ctx.actor
            )
        await _activity(ctx, f"AI agent handed off to a teammate ({reason}).")
        await _visitor_notice(ctx, "You're being connected to a teammate — they'll reply here.")
    await sink.add("handoff", output={"reason": reason})
    return await _complete(ctx, sink, status)


async def _fail(ctx: ToolContext, sink: _StepSink, error: str) -> ExecutionResult:
    ctx.run.error = error[:1000]
    await sink.add("error", output={"error": error[:500]})
    if not ctx.sandbox and ctx.conversation is not None and ctx.conversation.status == "pending":
        await conversations_service.update_status(
            ctx.session, ctx.conversation, "open", actor=ctx.actor
        )
        await _activity(ctx, "AI agent failed — waiting for a teammate.")
        await _visitor_notice(ctx, "You're being connected to a teammate — they'll reply here.")
    await sink.add("handoff", output={"reason": "provider failure"})
    return await _complete(ctx, sink, "failed")


async def _complete(
    ctx: ToolContext, sink: _StepSink, status: str, *, reply: str | None = None
) -> ExecutionResult:
    ctx.run.status = status
    ctx.run.finished_at = utcnow()
    ctx.run.lease_expires_at = None
    await _set_agent_typing(ctx, False)
    if not ctx.sandbox:
        await ctx.session.flush()
        await emit(
            ctx.session,
            Event(
                name=EventNames.AGENT_RUN_COMPLETED,
                workspace_id=ctx.run.workspace_id,
                payload={
                    "run_id": ctx.run.id,
                    "conversation_id": ctx.run.conversation_id,
                    "status": status,
                },
                actor=ctx.actor,
            ),
        )
        await broadcast(
            workspace_topic(ctx.run.workspace_id),
            "agent_run.updated",
            {"run_id": ctx.run.id, "status": status, "conversation_id": ctx.run.conversation_id},
        )
        await _publish_run_status(ctx, terminal=True)
        await _chain_next_queued(ctx)
    return ExecutionResult(status, reply, sink.sandbox_steps, _run_citations(ctx.run))


async def _chain_next_queued(ctx: ToolContext) -> None:
    """Kick the oldest queued follow-up run once this one is terminal.

    Follow-ups are created (not executed) when a visitor message lands while a
    run is in flight — see `_on_message_created`. This hook is what guarantees
    the second message is answered; the reaper only backstops a lost enqueue.
    """
    rows = (
        await ctx.session.execute(
            select(AgentRun.id, AgentRun.created_at)
            .where(
                AgentRun.conversation_id == ctx.run.conversation_id,
                AgentRun.id != ctx.run.id,
                AgentRun.status == "queued",
            )
            .order_by(AgentRun.created_at, AgentRun.id)
        )
    ).all()
    if rows:
        await enqueue("execute_agent_run", run_id=rows[0][0])


async def _activity(ctx: ToolContext, text: str) -> None:
    assert ctx.conversation is not None
    await conversations_service.add_message(
        ctx.session,
        ctx.conversation,
        direction="out",
        author_type="agent",
        author_id=ctx.agent.id,
        author_name=ctx.agent.name,
        content=text,
        visibility="activity",
        actor=ctx.actor,
        deliver=False,
    )


async def _set_agent_typing(ctx: ToolContext, is_typing: bool) -> None:
    """Show/hide the 'AI is typing' dots in the widget while a run is active.
    Fire-and-forget realtime only — never persisted, safe to miss."""
    if ctx.sandbox or ctx.conversation is None:
        return
    await broadcast(
        conversation_topic(ctx.conversation.id),
        "typing",
        {
            "conversation_id": ctx.conversation.id,
            "is_typing": is_typing,
            "source": "agent",
            "author_name": ctx.agent.name,
        },
    )


async def _visitor_notice(ctx: ToolContext, text: str) -> None:
    """A short, public, system-authored line the visitor actually sees — so a
    handoff isn't just silence. Distinct from `_activity` (agent-only trace):
    this one is `visibility=public`, `author_type=system`, and the widget
    renders it as a centered status line, not a chat bubble."""
    assert ctx.conversation is not None
    await conversations_service.add_message(
        ctx.session,
        ctx.conversation,
        direction="out",
        author_type="system",
        author_id=None,
        author_name="",
        content=text,
        visibility="public",
        actor=ctx.actor,
        deliver=False,  # in-app only; don't email/SMS a "connecting…" notice
    )


async def _notify_approvers(
    session: AsyncSession, workspace_id: str, approval: ApprovalRequest, agent: Agent
) -> None:
    memberships = (
        (await session.execute(select(Membership).where(Membership.workspace_id == workspace_id)))
        .scalars()
        .all()
    )
    for membership in memberships:
        custom = (
            list(membership.custom_role.permissions)
            if membership.role == "custom" and membership.custom_role is not None
            else None
        )
        if Perm.AI_APPROVE not in resolve_permissions(membership.role, custom):
            continue
        await notify(
            session,
            workspace_id,
            membership.user_id,
            type="approval",
            title=f"{agent.name} needs your approval",
            body=f"Approve “{approval.tool_key}” before it runs.",
            link="/ai/approvals",
            meta={"approval_id": approval.id, "conversation_id": approval.conversation_id},
        )


# ---------------------------------------------------------------------------
# approvals: decide + lazy expiry
# ---------------------------------------------------------------------------


async def decide_approval(
    session: AsyncSession,
    approval: ApprovalRequest,
    *,
    approved: bool,
    decided_by: str | None,
    note: str | None = None,
) -> ApprovalRequest:
    """Record a decision and re-enqueue the run to resume (approve/reject/expire)."""
    if approval.status != "pending":
        raise ConflictError(f"Approval already {approval.status}")
    now = utcnow()
    expired = approval.expires_at <= now
    if expired:
        approval.status = "expired"
    else:
        approval.status = "approved" if approved else "rejected"
        approval.decided_by = decided_by
        approval.note = note
    approval.decided_at = now
    await session.flush()

    ord = await _max_ord(session, approval.run_id) + 1
    session.add(
        AgentStep(
            id=uuid7(),
            workspace_id=approval.workspace_id,
            run_id=approval.run_id,
            ord=ord,
            kind="approval_decision",
            name=approval.tool_key,
            output={
                "status": approval.status,
                "approved": approved and not expired,
                "note": note,
                "decided_by": decided_by,
            },
            created_at=now,
        )
    )
    await session.flush()

    await broadcast(
        workspace_topic(approval.workspace_id),
        "approval.decided",
        {"approval_id": approval.id, "status": approval.status, "run_id": approval.run_id},
    )
    await emit(
        session,
        Event(
            name=EventNames.APPROVAL_DECIDED,
            workspace_id=approval.workspace_id,
            payload={
                "approval_id": approval.id,
                "run_id": approval.run_id,
                "status": approval.status,
            },
            actor=Actor(type="user", id=decided_by),
        ),
    )
    await enqueue("execute_agent_run", run_id=approval.run_id)
    return approval


async def expire_overdue(session: AsyncSession, workspace_id: str) -> list[ApprovalRequest]:
    """Lazily expire overdue pending approvals and resume their runs as rejected."""
    now = utcnow()
    overdue = (
        (
            await session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.workspace_id == workspace_id,
                    ApprovalRequest.status == "pending",
                    ApprovalRequest.expires_at <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    for approval in overdue:
        approval.status = "expired"
        approval.decided_at = now
        ord = await _max_ord(session, approval.run_id) + 1
        session.add(
            AgentStep(
                id=uuid7(),
                workspace_id=approval.workspace_id,
                run_id=approval.run_id,
                ord=ord,
                kind="approval_decision",
                name=approval.tool_key,
                output={"status": "expired"},
                created_at=now,
            )
        )
        await enqueue("execute_agent_run", run_id=approval.run_id)
    if overdue:
        await session.flush()
    return list(overdue)


# ---------------------------------------------------------------------------
# event triggers (registered at import)
# ---------------------------------------------------------------------------


@on(EventNames.CONVERSATION_CREATED)
async def _on_conversation_created(session: AsyncSession, event: Event) -> None:
    conversation = await session.get(Conversation, event.payload.get("conversation_id"))
    if conversation is None:
        return
    inbox = await session.get(Inbox, conversation.inbox_id)
    if inbox is None:
        return
    agent_id = (inbox.config or {}).get("ai_agent_id")
    if not agent_id:
        return
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != conversation.workspace_id or agent.status != "live":
        return
    conversation.ai_agent_id = agent_id
    if conversation.status == "open":
        conversation.status = "pending"
    await session.flush()
    await conversations_service.add_message(
        session,
        conversation,
        direction="out",
        author_type="agent",
        author_id=agent.id,
        author_name=agent.name,
        content=f"{agent.name} joined the conversation",
        visibility="activity",
        actor=Actor(type="agent", id=agent.id, label=agent.name),
        deliver=False,
    )


@on(EventNames.MESSAGE_CREATED)
async def _on_message_created(session: AsyncSession, event: Event) -> None:
    """Every inbound visitor message on an AI-owned thread gets exactly one run.

    The old guard simply returned when any run was active, which DROPPED the
    message: the in-flight run answered the previous message and the new one
    was never addressed (dogfood conversation #9 — the English KB question that
    "got" a reply to the earlier replay request). Now:

    - an in-flight run (running / awaiting_*) → a follow-up run is CREATED but
      not executed; `_complete` chains it, so the thread answers sequentially
      and never concurrently;
    - a still-queued run → cancel-and-merge: the queued run is superseded by
      one covering both messages (history includes both, one reply);
    - no active run → create + enqueue, as before.
    """
    payload = event.payload
    if payload.get("direction") != "in":
        return
    conversation = await session.get(Conversation, payload.get("conversation_id"))
    if conversation is None or not conversation.ai_agent_id or conversation.status != "pending":
        return
    message = await session.get(Message, payload.get("message_id"))
    if message is None or message.visibility != "public":
        return
    agent = await session.get(Agent, conversation.ai_agent_id)
    if agent is None or agent.status != "live":
        return
    active_runs = (
        (
            await session.execute(
                select(AgentRun).where(
                    AgentRun.conversation_id == conversation.id,
                    AgentRun.status.in_(_ACTIVE_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    now = utcnow()
    in_flight = [r for r in active_runs if r.status != "queued"]
    for superseded in (r for r in active_runs if r.status == "queued"):
        superseded.status = "canceled"
        superseded.error = "superseded by a newer visitor message (merged into one reply)"
        superseded.finished_at = now
    run = AgentRun(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        agent_id=agent.id,
        trigger_message_id=message.id,
        status="queued",
    )
    session.add(run)
    await session.flush()
    if not in_flight:
        await enqueue("execute_agent_run", run_id=run.id)
    # else: `_complete` of the in-flight run chains this one (reaper backstops).


async def reap_stalled_runs(session: AsyncSession) -> list[AgentRun]:
    """Hard server-side timeout: no run may hold a thread (or a widget status
    indicator) forever.

    - "running" past its lease (+grace): the worker died or hung. Re-enqueuing
      routes it through `_claim` → "crashed" → `_fail`, which posts the visitor
      notice and broadcasts a TERMINAL status — the stale "working on the
      page…" can then never outlive its run.
    - "queued" older than `QUEUED_STALL_SECONDS`: a lost enqueue or a follow-up
      whose chaining trigger vanished. Re-enqueued; the in-order guard inside
      `execute_run` keeps it waiting if an earlier run is genuinely active.
    """
    now = utcnow()
    lease_cutoff = now - timedelta(seconds=LEASE_REAP_GRACE_SECONDS)
    queued_cutoff = now - timedelta(seconds=QUEUED_STALL_SECONDS)
    stalled = (
        (
            await session.execute(
                select(AgentRun).where(
                    (
                        (AgentRun.status == "running")
                        & (AgentRun.lease_expires_at.is_not(None))
                        & (AgentRun.lease_expires_at <= lease_cutoff)
                    )
                    | ((AgentRun.status == "queued") & (AgentRun.updated_at <= queued_cutoff))
                )
            )
        )
        .scalars()
        .all()
    )
    for run in stalled:
        await enqueue("execute_agent_run", run_id=run.id)
    return list(stalled)


@scheduled("agent_client_wait_sweep", every_seconds=30)
async def _client_wait_sweep_job() -> None:
    """Unstick runs whose in-app page op was never answered."""
    from app.core.db import session_scope

    async with session_scope() as session:
        await sweep_stale_client_waits(session)


@scheduled("agent_run_reaper", every_seconds=60)
async def _run_reaper_job() -> None:
    """Terminal status for every run, eventually — whatever died in between."""
    from app.core.db import session_scope

    async with session_scope() as session:
        await reap_stalled_runs(session)


# Registers the "execute_agent_run" queue task (kept in tasks.py per file ownership).
from app.agents import tasks as _tasks  # noqa: E402,F401
