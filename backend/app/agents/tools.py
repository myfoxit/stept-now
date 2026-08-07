"""Tool registry + executors for the agent engine.

Seven builtin tools, the client-executed page tools, plus workspace
`CustomAction` HTTP tools. Each tool has:
- a `ToolSpec` (name/description/JSON-schema) sent to the provider,
- a default policy (auto | require_approval | disabled — the contract's
  DEFAULT_POLICIES), overridable per-agent,
- a `control` signal (``none`` | ``handoff`` | ``close``) so terminal tools stop
  the loop.

Tools in `ToolPlan.client` are NOT executed here: the engine defers them to the
visitor's browser (see `app.agents.page_tools`) and resumes with the result.

Custom actions template `{param}` placeholders into the URL/body, enforce that the
final (and any redirect) host equals the configured URL host, decrypt header
values only at call time, and validate input against ``params_schema`` with a
dependency-free JSON-Schema subset (no ``jsonschema`` library available).

Sandbox mode makes the mutating builtins (handoff/close/tag/note/collect) return
``{"dry_run": true, ...}`` without touching the conversation; search + custom
actions run for real (still host-enforced).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import page_tools
from app.agents.guides import search_guides
from app.ai.base import ToolSpec
from app.core.events import Actor
from app.core.net import UnsafeUrlError, assert_public_url
from app.core.security import decrypt_secret
from app.models.agent import Agent, CustomAction
from app.models.agent_run import AgentRun
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.tag import Tag
from app.rag.context import DEFAULT_MAX_TOKENS, build_context, guard_retrieved
from app.rag.retrieval import search_chunks
from app.services import conversations as conversations_service
from app.services.search_analytics import record_search

# --- policies ---------------------------------------------------------------

POLICY_AUTO = "auto"
POLICY_REQUIRE_APPROVAL = "require_approval"
POLICY_DISABLED = "disabled"

ACTION_PREFIX = "action:"

#: Turns handed to the query rewriter for pronoun resolution.
_HISTORY_TURNS = 4


# --- context & outcome ------------------------------------------------------


@dataclass
class ToolContext:
    session: AsyncSession
    workspace_id: str
    run: AgentRun
    agent: Agent
    conversation: Conversation | None
    actor: Actor
    mode: str = "live"  # "live" | "sandbox"
    action_ids: dict[str, str] = field(default_factory=dict)  # spec name -> CustomAction id

    @property
    def sandbox(self) -> bool:
        return self.mode == "sandbox"


@dataclass
class ToolOutcome:
    result: dict[str, Any]
    is_error: bool = False
    control: str = "none"  # "none" | "handoff" | "close"
    reply_message_id: str | None = None


@dataclass
class ActionResult:
    ok: bool
    status: int | None = None
    body: str | None = None
    error: str | None = None


ToolExecutor = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolOutcome]]


@dataclass
class BuiltinTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    executor: ToolExecutor
    default_policy: str = POLICY_AUTO
    control: str = "none"


@dataclass
class ToolPlan:
    specs: list[ToolSpec]
    policy: dict[str, str]
    control: dict[str, str]
    action_ids: dict[str, str]
    #: Tool names the engine must defer to the visitor's browser instead of
    #: executing server-side (`app.agents.page_tools`).
    client: set[str] = field(default_factory=set)


# --- helpers ----------------------------------------------------------------


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


def _retrieval_settings(agent: Agent) -> tuple[int, list[str] | None]:
    settings = agent.settings if isinstance(agent.settings, dict) else {}
    retrieval = settings.get("retrieval") or {}
    k = retrieval.get("k") or 6
    source_ids = retrieval.get("source_ids")
    if source_ids is not None and not isinstance(source_ids, list):
        source_ids = None
    return int(k), source_ids


# --- builtin executors ------------------------------------------------------


async def _exec_search_knowledge(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolOutcome:
    query = str(tool_input.get("query", "")).strip()
    if not query:
        ctx.run.citations = []
        return ToolOutcome({"results": []})
    k, source_ids = _retrieval_settings(ctx.agent)
    history = await _recent_turns(ctx)
    # Answer path: rerank is on (the pass self-gates to >5 fused candidates and
    # degrades to the fused order on any failure — see app.rag.rerank).
    results = await search_chunks(
        ctx.session,
        ctx.workspace_id,
        query,
        k=k,
        source_ids=source_ids,
        rerank=True,
        history=history,
    )
    await record_search(
        ctx.session,
        ctx.workspace_id,
        query=query,
        source="agent",
        results_count=len(results),
        top_score=results[0].score if results else None,
    )
    # Budgeted assembly rather than a fixed slice per chunk: the best chunk gets
    # the room it needs, and the citation numbering the model sees is the same
    # numbering the widget renders under the reply.
    context = build_context(results, query, max_tokens=_context_budget(ctx.agent))
    ctx.run.citations = context.citation_dicts()
    payload: dict[str, Any] = {
        "results": [
            {
                "n": citation.n,
                "title": citation.title,
                "content": citation.content,
                "url": citation.url,
            }
            for citation in context.citations
        ]
    }
    if context.context_text:
        # The full budgeted text the model should ground on, wrapped once as
        # untrusted material ("results" stays the compact [n] citation index).
        payload["context"] = guard_retrieved(context.context_text)
    return ToolOutcome(payload)


def _context_budget(agent: Agent) -> int:
    settings = agent.settings if isinstance(agent.settings, dict) else {}
    retrieval = settings.get("retrieval") or {}
    try:
        return max(200, int(retrieval.get("context_tokens") or DEFAULT_MAX_TOKENS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS


async def _recent_turns(ctx: ToolContext) -> list[str]:
    """The last few public turns, oldest first — pronoun resolution material.

    "How do I cancel it?" is only answerable against the previous message, so the
    rewriter gets the conversation, not just the tool argument.
    """
    if ctx.conversation is None:
        return []
    from app.models.message import Message

    rows = (
        (
            await ctx.session.execute(
                select(Message.content)
                .where(
                    Message.conversation_id == ctx.conversation.id,
                    Message.visibility == "public",
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(_HISTORY_TURNS)
            )
        )
        .scalars()
        .all()
    )
    return list(reversed([content for content in rows if content]))


async def _exec_find_guide(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolOutcome:
    """Look for a published tour/checklist that teaches what the visitor asked.

    Scoped to the page the visitor is on when the widget reported one, so the
    same question on two screens can resolve to two different guides.
    """
    query = str(tool_input.get("query", "")).strip()
    if not query:
        return ToolOutcome({"guides": []})
    attributes = (
        ctx.conversation.attributes
        if ctx.conversation is not None and isinstance(ctx.conversation.attributes, dict)
        else {}
    )
    url = attributes.get("page_url")
    matches = await search_guides(
        ctx.session, ctx.workspace_id, query, url=url if isinstance(url, str) else None
    )
    return ToolOutcome({"guides": [match.to_tool_payload() for match in matches]})


async def _exec_handoff(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolOutcome:
    reason = str(tool_input.get("reason", "")).strip()
    if ctx.sandbox:
        return ToolOutcome(
            {"dry_run": True, "ok": True, "note": "would hand off to a human", "reason": reason},
            control="handoff",
        )
    assert ctx.conversation is not None
    await conversations_service.update_status(
        ctx.session, ctx.conversation, "open", actor=ctx.actor
    )
    await _activity(
        ctx, f"Handed off to a teammate: {reason}" if reason else "Handed off to a teammate"
    )
    return ToolOutcome({"ok": True, "note": "conversation handed to a human"}, control="handoff")


async def _exec_close(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolOutcome:
    closing = str(tool_input.get("closing_message", "")).strip()
    if ctx.sandbox:
        return ToolOutcome(
            {
                "dry_run": True,
                "ok": True,
                "note": "would close the conversation",
                "closing_message": closing,
            },
            control="close",
        )
    assert ctx.conversation is not None
    reply_message_id: str | None = None
    if closing:
        message = await conversations_service.add_message(
            ctx.session,
            ctx.conversation,
            direction="out",
            author_type="agent",
            author_id=ctx.agent.id,
            author_name=ctx.agent.name,
            content=closing,
            actor=ctx.actor,
        )
        reply_message_id = message.id
    await conversations_service.update_status(
        ctx.session, ctx.conversation, "resolved", actor=ctx.actor
    )
    return ToolOutcome({"ok": True}, control="close", reply_message_id=reply_message_id)


async def _exec_tag(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolOutcome:
    tag_name = str(tool_input.get("tag_name", "")).strip()
    if ctx.sandbox:
        return ToolOutcome({"dry_run": True, "ok": True, "tag_name": tag_name})
    assert ctx.conversation is not None
    tag = (
        await ctx.session.execute(
            select(Tag).where(Tag.workspace_id == ctx.workspace_id, Tag.name == tag_name)
        )
    ).scalar_one_or_none()
    if tag is None:
        return ToolOutcome({"error": "unknown tag"}, is_error=True)
    await conversations_service.add_tag(ctx.session, ctx.conversation, tag.id, actor=ctx.actor)
    return ToolOutcome({"ok": True})


async def _exec_collect(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolOutcome:
    email = tool_input.get("email")
    name = tool_input.get("name")
    if ctx.sandbox:
        return ToolOutcome({"dry_run": True, "ok": True, "email": email, "name": name})
    assert ctx.conversation is not None
    contact = await ctx.session.get(Contact, ctx.conversation.contact_id)
    changed: list[str] = []
    if contact is not None:
        if email and contact.email != email:
            contact.email = str(email)
            changed.append("email")
        if name and contact.name != name:
            contact.name = str(name)
            changed.append("name")
        if changed:
            await ctx.session.flush()
            await _activity(ctx, f"Updated contact details ({', '.join(changed)})")
    return ToolOutcome({"ok": True, "updated": changed})


async def _exec_note(ctx: ToolContext, tool_input: dict[str, Any]) -> ToolOutcome:
    text = str(tool_input.get("text", "")).strip()
    if ctx.sandbox:
        return ToolOutcome({"dry_run": True, "ok": True, "text": text})
    assert ctx.conversation is not None
    if text:
        await conversations_service.add_message(
            ctx.session,
            ctx.conversation,
            direction="out",
            author_type="agent",
            author_id=ctx.agent.id,
            author_name=ctx.agent.name,
            content=text,
            visibility="note",
            actor=ctx.actor,
            deliver=False,
        )
    return ToolOutcome({"ok": True})


async def _exec_action(ctx: ToolContext, name: str, tool_input: dict[str, Any]) -> ToolOutcome:
    action_id = ctx.action_ids.get(name)
    if action_id is None:
        return ToolOutcome({"error": "unknown action"}, is_error=True)
    action = await ctx.session.get(CustomAction, action_id)
    if action is None or action.workspace_id != ctx.workspace_id:
        return ToolOutcome({"error": "unknown action"}, is_error=True)
    result = await execute_custom_action(action, tool_input)
    if not result.ok:
        return ToolOutcome({"error": result.error}, is_error=True)
    return ToolOutcome({"status": result.status, "body": result.body})


# --- builtin registry -------------------------------------------------------

BUILTIN_TOOLS: dict[str, BuiltinTool] = {
    "search_knowledge": BuiltinTool(
        name="search_knowledge",
        description="Search the workspace knowledge base and return passages to cite as [n].",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to look up."}},
            "required": ["query"],
        },
        executor=_exec_search_knowledge,
        default_policy=POLICY_AUTO,
    ),
    "find_guide": BuiltinTool(
        name="find_guide",
        description=(
            "Look for a product tour or checklist that walks the person through a task in this "
            "app. Call it whenever they ask how to do something — a guide that shows them in the "
            "real UI beats a written answer. Returns ids to pass to show_guide."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The task, in the person's words."}
            },
            "required": ["query"],
        },
        executor=_exec_find_guide,
        default_policy=POLICY_AUTO,
    ),
    "handoff_to_human": BuiltinTool(
        name="handoff_to_human",
        description="Hand the conversation to a human teammate when you cannot help confidently.",
        input_schema={
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "Why you are handing off."}},
        },
        executor=_exec_handoff,
        default_policy=POLICY_AUTO,
        control="handoff",
    ),
    "tag_conversation": BuiltinTool(
        name="tag_conversation",
        description="Apply an existing tag to this conversation.",
        input_schema={
            "type": "object",
            "properties": {"tag_name": {"type": "string"}},
            "required": ["tag_name"],
        },
        executor=_exec_tag,
        default_policy=POLICY_AUTO,
    ),
    "close_conversation": BuiltinTool(
        name="close_conversation",
        description="Resolve the conversation, optionally sending a final message first.",
        input_schema={
            "type": "object",
            "properties": {"closing_message": {"type": "string"}},
        },
        executor=_exec_close,
        default_policy=POLICY_REQUIRE_APPROVAL,
        control="close",
    ),
    "collect_contact_details": BuiltinTool(
        name="collect_contact_details",
        description="Save the contact's email and/or name when they provide it.",
        input_schema={
            "type": "object",
            "properties": {"email": {"type": "string"}, "name": {"type": "string"}},
        },
        executor=_exec_collect,
        default_policy=POLICY_AUTO,
    ),
    "note_to_team": BuiltinTool(
        name="note_to_team",
        description="Leave an internal note for the team (not shown to the customer).",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        executor=_exec_note,
        default_policy=POLICY_AUTO,
    ),
}

# Contract DEFAULT_POLICIES (action:* handled via ACTION_DEFAULT_POLICY).
DEFAULT_POLICIES: dict[str, str] = {name: bt.default_policy for name, bt in BUILTIN_TOOLS.items()}
ACTION_DEFAULT_POLICY = POLICY_REQUIRE_APPROVAL


# --- resolution & dispatch --------------------------------------------------


async def resolve_agent_tools(
    session: AsyncSession,
    workspace_id: str,
    agent: Agent,
    *,
    conversation: Conversation | None = None,
) -> ToolPlan:
    """Build the ToolSpec list + policy/control/action maps for one agent.

    All builtins are offered to the model (policy governs execution: a disabled
    tool call yields a denial-as-tool-result); custom actions are opt-in via
    ``action:<id>`` keys in ``agent.tools``.

    In-app page tools are different in kind: they are only offered when the agent
    enables page control AND the visitor consented in this conversation, because
    they act inside someone else's session. Withholding the spec (rather than
    refusing the call) means the model never proposes what it cannot do.
    """
    # Explicit per-tool policy from the agent config (None → apply the default).
    configured: dict[str, str | None] = {}
    for entry in agent.tools or []:
        if isinstance(entry, dict) and entry.get("key"):
            raw_policy = entry.get("policy")
            configured[str(entry["key"])] = str(raw_policy) if raw_policy else None

    specs: list[ToolSpec] = []
    policy: dict[str, str] = {}
    control: dict[str, str] = {}
    action_ids: dict[str, str] = {}

    for name, builtin in BUILTIN_TOOLS.items():
        specs.append(
            ToolSpec(name=name, description=builtin.description, input_schema=builtin.input_schema)
        )
        policy[name] = configured.get(name) or builtin.default_policy
        control[name] = builtin.control

    client: set[str] = set()
    offer_client, allow_mutating = page_tools.client_tools_available(
        agent.settings, conversation.attributes if conversation is not None else None
    )
    if offer_client:
        for client_spec in page_tools.client_tool_specs(allow_mutating=allow_mutating):
            specs.append(client_spec)
            policy[client_spec.name] = configured.get(client_spec.name) or POLICY_AUTO
            control[client_spec.name] = "none"
            client.add(client_spec.name)

    for key, pol in configured.items():
        if not key.startswith(ACTION_PREFIX):
            continue
        action_id = key[len(ACTION_PREFIX) :]
        action = await session.get(CustomAction, action_id)
        if action is None or action.workspace_id != workspace_id:
            continue
        spec_name = action.name
        schema = action.params_schema or {"type": "object", "properties": {}}
        specs.append(
            ToolSpec(
                name=spec_name,
                description=action.description or f"Custom action {spec_name}",
                input_schema=schema,
            )
        )
        policy[spec_name] = pol or ACTION_DEFAULT_POLICY
        control[spec_name] = "none"
        action_ids[spec_name] = action_id

    return ToolPlan(
        specs=specs, policy=policy, control=control, action_ids=action_ids, client=client
    )


async def execute_tool(ctx: ToolContext, name: str, tool_input: dict[str, Any]) -> ToolOutcome:
    builtin = BUILTIN_TOOLS.get(name)
    if builtin is not None:
        return await builtin.executor(ctx, tool_input)
    if name in ctx.action_ids:
        return await _exec_action(ctx, name, tool_input)
    return ToolOutcome({"error": f"unknown tool: {name}"}, is_error=True)


# --- custom action execution ------------------------------------------------

_PARAM_RE = re.compile(r"\{(\w+)\}")
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def validate_params(schema: dict[str, Any] | None, params: dict[str, Any]) -> str | None:
    """Dependency-free JSON-Schema subset: required keys + top-level type checks."""
    if not isinstance(schema, dict) or not schema:
        return None
    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if key not in params:
                return f"missing required parameter: {key}"
    props = schema.get("properties")
    if isinstance(props, dict):
        for key, spec in props.items():
            if key not in params or not isinstance(spec, dict) or "type" not in spec:
                continue
            expected = _TYPE_MAP.get(str(spec["type"]))
            if expected is None:
                continue
            value = params[key]
            # bool is a subclass of int — never accept it for number/integer.
            if spec["type"] in ("number", "integer") and isinstance(value, bool):
                return f"parameter '{key}' must be of type {spec['type']}"
            if not isinstance(value, expected):
                return f"parameter '{key}' must be of type {spec['type']}"
    return None


def template_string(template: str, params: dict[str, Any], *, url_encode: bool = False) -> str:
    def repl(match: re.Match[str]) -> str:
        value = params.get(match.group(1), "")
        text = "" if value is None else str(value)
        return quote(text, safe="") if url_encode else text

    return _PARAM_RE.sub(repl, template)


async def execute_custom_action(
    action: CustomAction, params: dict[str, Any], *, client: httpx.AsyncClient | None = None
) -> ActionResult:
    """Validate, template, host-enforce, and issue one HTTP request for a custom action."""
    error = validate_params(action.params_schema, params)
    if error:
        return ActionResult(ok=False, error=error)

    allowed_host = urlparse(action.url).hostname
    templated_url = template_string(action.url, params, url_encode=True)
    target_host = urlparse(templated_url).hostname
    if not allowed_host or target_host != allowed_host:
        return ActionResult(
            ok=False,
            error=f"blocked: request host {target_host!r} is not the allowed host {allowed_host!r}",
        )
    # The result body is handed back to the agent, so this must hold at request
    # time and not only when the action was saved (DNS moves; rows predate the
    # schema guard).
    try:
        assert_public_url(templated_url)
    except UnsafeUrlError as exc:
        return ActionResult(ok=False, error=f"blocked: {exc}")

    headers: dict[str, str] = {}
    for header, value in (action.headers or {}).items():
        try:
            headers[header] = decrypt_secret(value)
        except Exception:  # noqa: BLE001 — tolerate a plaintext/rotated value, never crash a run
            headers[header] = value
    body = template_string(action.body_template, params) if action.body_template else None

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=float(action.timeout_s), follow_redirects=False)
    try:
        response = await client.request(
            action.method.upper(), templated_url, headers=headers or None, content=body
        )
    except httpx.HTTPError as exc:
        return ActionResult(ok=False, error=f"request failed: {type(exc).__name__}: {exc}")
    finally:
        if owns_client:
            await client.aclose()

    if response.is_redirect:
        location = response.headers.get("location", "")
        redirect_host = urlparse(urljoin(templated_url, location)).hostname
        if redirect_host != allowed_host:
            return ActionResult(
                ok=False, error=f"blocked redirect to disallowed host {redirect_host!r}"
            )

    return ActionResult(ok=True, status=response.status_code, body=response.text[:2000])
