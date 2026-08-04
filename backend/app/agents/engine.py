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

from app.agents import page_tools
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
from app.core.permissions import Perm, resolve_permissions
from app.core.queue import enqueue
from app.core.scheduler import scheduled
from app.models.agent import Agent
from app.models.agent_run import AgentRun, AgentStep, ApprovalRequest
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.message import Message
from app.models.workspace import Membership, Workspace
from app.realtime.manager import broadcast, conversation_topic, workspace_topic
from app.services import conversations as conversations_service
from app.services.notifications import notify

LEASE_SECONDS = 120
APPROVAL_TTL_HOURS = 24
HISTORY_CAP = 30
#: How long a run may sit waiting for the visitor's browser before we give up on
#: it. Generous enough for a slow page + a `page_wait`, short enough that a
#: closed tab does not leave the person staring at a silent thread.
CLIENT_OP_TIMEOUT_SECONDS = 90
_TERMINAL_STATUSES = frozenset({"completed", "failed", "handed_off", "canceled"})
_CITATION_RE = re.compile(r"\[(\d+)\]")


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
    parts.extend(_page_control_prompt(conversation, plan))
    return "\n\n".join(parts)


def _page_control_prompt(
    conversation: Conversation | None, plan: tool_registry.ToolPlan | None
) -> list[str]:
    """The in-app-guidance half of the prompt.

    Only emitted when the page tools are actually in the plan — a model told it can
    walk someone through the UI, that then has no such tool, produces confident
    promises it cannot keep. The ladder (show a tour → show your own steps → do it)
    is spelled out because the default failure mode is the opposite: a model that
    explains in prose when it could point, and clicks when it should have asked.
    """
    if plan is None or not plan.client:
        return []
    parts: list[str] = []
    where = _page_context_line(conversation)
    if where:
        parts.append(where)
    ladder = [
        "You are embedded IN the app the person is using, so you can show them things "
        "rather than only describing them. When they ask how to do something:",
        "1. call find_guide — if a published tour covers it, play it with show_guide and "
        "say in one line what it will walk them through;",
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
    ctx = _context(session, run, agent, conversation, actor, mode, plan.action_ids)
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
        messages = [
            ChatMessage.system(
                compose_system_prompt(agent, workspace_name, conversation=conversation, plan=plan)
            )
        ]
        messages.extend(await build_history(session, conversation))

    # --- main loop ---
    max_calls = _max_tool_calls(agent)
    tool_calls_used = 0 if sandbox else await _executed_tool_calls(session, run.id)
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
            client_error = await _reject_client_call(ctx, sink, messages, call)
            if client_error is not None:
                continue
            if sandbox:
                # A dry run has no browser on the other end; report what WOULD
                # have been asked of the page so the trace still reads honestly.
                await sink.add("tool_call", name=call.name, input=call.input)
                dry = {"dry_run": True, **page_tools.op_for(call.name, call.input)}
                await sink.add("tool_result", name=call.name, output=dry)
                messages.append(ChatMessage.tool_result(call.id, json.dumps(dry)))
                continue
            return await _pause_for_client(ctx, sink, messages, call)

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
                agent, workspace_name, conversation=conversation, plan=sandbox_plan
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
    )


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
    return ExecutionResult("awaiting_approval", None, sink.sandbox_steps, _run_citations(ctx.run))


async def _reject_client_call(
    ctx: ToolContext, sink: _StepSink, messages: list[ChatMessage], call: ToolCall
) -> str | None:
    """Answer a malformed or over-budget page call locally; None means "send it".

    Two guards, both cheaper than a browser round-trip: schema validation, and a
    per-run cap on ops that change the visitor's app. The cap counts what already
    happened in this run's trace, so it survives the pause/resume cycle that every
    client op goes through.
    """
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


async def _pause_for_client(
    ctx: ToolContext, sink: _StepSink, messages: list[ChatMessage], call: ToolCall
) -> ExecutionResult:
    """Hand a page op to the visitor's widget and park the run until it answers."""
    op = page_tools.op_for(call.name, call.input)
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
    await ctx.session.flush()

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
    return ExecutionResult("awaiting_client", None, sink.sandbox_steps, _run_citations(ctx.run))


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
    for run in stale:
        pending = run.pending_tool_call or {}
        if "result" in pending:
            continue  # a result landed; the queued resume will pick it up
        run.pending_tool_call = {**pending, "result": {"error": "timed out waiting for the page"}}
        await enqueue("execute_agent_run", run_id=run.id)
    if stale:
        await session.flush()
    return list(stale)


async def _final_reply(ctx: ToolContext, sink: _StepSink, content: str) -> ExecutionResult:
    cleaned, referenced = apply_citations(content, _run_citations(ctx.run))
    if not ctx.sandbox and ctx.conversation is not None:
        message = await conversations_service.add_message(
            ctx.session,
            ctx.conversation,
            direction="out",
            author_type="agent",
            author_id=ctx.agent.id,
            author_name=ctx.agent.name,
            content=cleaned,
            actor=ctx.actor,
            meta={"agent_run_id": ctx.run.id, "citations": referenced},
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
    await sink.add("handoff", output={"reason": "provider failure"})
    return await _complete(ctx, sink, "failed")


async def _complete(
    ctx: ToolContext, sink: _StepSink, status: str, *, reply: str | None = None
) -> ExecutionResult:
    ctx.run.status = status
    ctx.run.finished_at = utcnow()
    ctx.run.lease_expires_at = None
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
    return ExecutionResult(status, reply, sink.sandbox_steps, _run_citations(ctx.run))


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
    active = (
        await session.execute(
            select(AgentRun.id)
            .where(
                AgentRun.conversation_id == conversation.id,
                AgentRun.status.in_(["queued", "running", "awaiting_approval", "awaiting_client"]),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active is not None:
        return
    run = AgentRun(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        agent_id=agent.id,
        trigger_message_id=message.id,
        status="queued",
    )
    session.add(run)
    await session.flush()
    await enqueue("execute_agent_run", run_id=run.id)


@scheduled("agent_client_wait_sweep", every_seconds=30)
async def _client_wait_sweep_job() -> None:
    """Unstick runs whose in-app page op was never answered."""
    from app.core.db import session_scope

    async with session_scope() as session:
        await sweep_stale_client_waits(session)


# Registers the "execute_agent_run" queue task (kept in tasks.py per file ownership).
from app.agents import tasks as _tasks  # noqa: E402,F401
