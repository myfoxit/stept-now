"""Client-executed tools: the agent's eyes and hands inside the visitor's page.

The old repo drove a *separate* browser tab from the server via an extension
gateway (`services/ai_tools/browser_*.py` → `extension/src/drive-controller.ts`).
An embedded widget already lives in the page the visitor is looking at, so the
same op set is turned inside out: the tool call is DEFERRED to the widget, the
widget executes it in the host DOM (`widget/src/page-agent.ts`), and the result
comes back as the tool result.

Mechanically this is the approval gate the engine already has — persist the
pending call plus a message snapshot, return, resume on an external event — with
the widget rather than a human supplying the answer. Nothing is held in memory,
so a worker restart mid-guide loses nothing.

Two tool families live here:

`page_*`  read and act on the current page.
`show_*`  put an overlay on the page: play a published tour, or walk the visitor
          through steps the model composed itself from a snapshot.

None of these are offered unless the agent has `settings.page_control.enabled`
AND the visitor consented for this conversation — see `client_tools_available`.
"""

from __future__ import annotations

from typing import Any

from app.ai.base import ToolSpec

# Tool names, grouped by what they let the model do.
PAGE_SNAPSHOT = "page_snapshot"
PAGE_FIND = "page_find"
PAGE_READ = "page_read"
PAGE_ACT = "page_act"
PAGE_NAVIGATE = "page_navigate"
PAGE_SCROLL = "page_scroll"
PAGE_WAIT = "page_wait"
SHOW_GUIDE = "show_guide"
SHOW_STEPS = "show_steps"

READ_ONLY_TOOLS = frozenset({PAGE_SNAPSHOT, PAGE_FIND, PAGE_READ, PAGE_SCROLL, PAGE_WAIT})
"""Ops that only look at the page — safe to run without a per-action gate."""

MUTATING_TOOLS = frozenset({PAGE_ACT, PAGE_NAVIGATE})
"""Ops that change the visitor's app state. Gated by consent, capped per run."""

GUIDE_TOOLS = frozenset({SHOW_GUIDE, SHOW_STEPS})
"""Ops that put a coach-mark overlay on the page. The visitor stays in control."""

CLIENT_TOOLS = READ_ONLY_TOOLS | MUTATING_TOOLS | GUIDE_TOOLS

ACT_KINDS = ("click", "fill", "select", "check", "uncheck", "hover", "submit")

# Cap on mutating ops in a single run: a model that loops on "click, no change,
# click" must run out of rope long before it has clicked 50 things in someone's
# production account.
MAX_MUTATING_OPS = 12

_SPECS: dict[str, ToolSpec] = {
    PAGE_SNAPSHOT: ToolSpec(
        name=PAGE_SNAPSHOT,
        description=(
            "Look at the page the person is on: returns the URL, the title and a numbered list "
            'of everything interactive — [3]<button name="Save">. The [index] is how you act on '
            "an element with page_act. Long pages are paged: pass the offset the truncation "
            "marker suggests to see more. Take a fresh snapshot after anything changes the page."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "offset": {
                    "type": "integer",
                    "description": "Continue the listing from this element position.",
                }
            },
        },
    ),
    PAGE_FIND: ToolSpec(
        name=PAGE_FIND,
        description=(
            "Find elements on the page whose label or text contains a string, and get their "
            "[index]. Use instead of reading a long snapshot when you already know what you are "
            "looking for. Also reports whether each match is on screen."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to look for."},
                "limit": {"type": "integer", "description": "Max matches (default 10)."},
            },
            "required": ["query"],
        },
    ),
    PAGE_READ: ToolSpec(
        name=PAGE_READ,
        description=(
            "Read the visible text of the page. Use it to answer questions about what the person "
            "is looking at, or to confirm an action landed (a success message, a new row)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "max_chars": {"type": "integer", "description": "Cap on returned text."}
            },
        },
    ),
    PAGE_ACT: ToolSpec(
        name=PAGE_ACT,
        description=(
            "Do something on the page for the person: click a button, fill a field, choose an "
            "option, tick a checkbox. Address the element by its [index] from the latest "
            "snapshot or page_find. Returns a fresh snapshot so you can see the result — and "
            "tells you when a click changed nothing visible. Only use this when the person asked "
            "you to do it for them; otherwise show them with show_steps."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Element [index] from a snapshot."},
                "kind": {
                    "type": "string",
                    "enum": list(ACT_KINDS),
                    "description": "What to do (default click).",
                },
                "text": {"type": "string", "description": "Text to type / option to choose."},
                "submit": {
                    "type": "boolean",
                    "description": "After filling, press Enter (submits most forms).",
                },
            },
            "required": ["index"],
        },
    ),
    PAGE_NAVIGATE: ToolSpec(
        name=PAGE_NAVIGATE,
        description=(
            "Take the person to another page of this same app (a path like /billing/invoices, or "
            "a full URL on this site). Other sites are refused. Follow with page_snapshot once "
            "the new page has loaded."
        ),
        input_schema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Path or same-site URL."}},
            "required": ["url"],
        },
    ),
    PAGE_SCROLL: ToolSpec(
        name=PAGE_SCROLL,
        description="Scroll the page when what you need is above or below the fold.",
        input_schema={
            "type": "object",
            "properties": {
                "dir": {"type": "string", "enum": ["up", "down"]},
                "amount": {"type": "integer", "description": "Pixels (default 600)."},
            },
        },
    ),
    PAGE_WAIT: ToolSpec(
        name=PAGE_WAIT,
        description="Wait briefly for the page to finish loading or saving, then re-snapshot.",
        input_schema={
            "type": "object",
            "properties": {"ms": {"type": "integer", "description": "Milliseconds (max 8000)."}},
        },
    ),
    SHOW_GUIDE: ToolSpec(
        name=SHOW_GUIDE,
        description=(
            "Play a published product tour on the page, so the person is walked through the real "
            "UI step by step. Prefer this over explaining in words when find_guide returned a "
            "tour that covers the question. Pass the tour id from find_guide."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tour_id": {"type": "string", "description": "Tour id from find_guide."},
            },
            "required": ["tour_id"],
        },
    ),
    SHOW_STEPS: ToolSpec(
        name=SHOW_STEPS,
        description=(
            "Walk the person through steps YOU compose, highlighting each element on the real "
            "page — for when no published tour covers the question. Take a page_snapshot first, "
            "then reference each element by its [index]. The person clicks through at their own "
            "pace and stays in control, which is usually better than doing it for them."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "What this walkthrough achieves."},
                "steps": {
                    "type": "array",
                    "description": "2-6 steps, in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {
                                "type": "integer",
                                "description": "Element [index] to highlight (omit to float).",
                            },
                            "title": {"type": "string", "description": "Short instruction."},
                            "body": {
                                "type": "string",
                                "description": "Optional detail shown under the title.",
                            },
                        },
                        "required": ["title"],
                    },
                },
            },
            "required": ["steps"],
        },
    ),
}


def spec(name: str) -> ToolSpec | None:
    return _SPECS.get(name)


def client_tool_specs(*, allow_mutating: bool) -> list[ToolSpec]:
    """Specs for the client tools an agent may use this run.

    Read + guide tools are always in the set once page control is on. The
    mutating pair is withheld entirely when the visitor has not consented — a
    withheld tool cannot be called at all, which is a stronger guarantee than
    refusing the call after the model has already decided to make it.
    """
    names = READ_ONLY_TOOLS | GUIDE_TOOLS
    if allow_mutating:
        names = names | MUTATING_TOOLS
    return [_SPECS[name] for name in _SPECS if name in names]


def page_control_settings(agent_settings: Any) -> dict[str, Any]:
    """Normalized `settings.page_control` block of an agent."""
    settings = agent_settings if isinstance(agent_settings, dict) else {}
    block = settings.get("page_control")
    return block if isinstance(block, dict) else {}


def page_control_enabled(agent_settings: Any) -> bool:
    """Is in-app guidance switched on for this agent? Off by default."""
    return bool(page_control_settings(agent_settings).get("enabled"))


def actions_allowed(agent_settings: Any) -> bool:
    """May this agent CHANGE the page, or only look and point?

    Separate from `enabled` on purpose: plenty of workspaces want "show me where
    to click" without ever letting an AI press the button.
    """
    block = page_control_settings(agent_settings)
    return bool(block.get("enabled")) and bool(block.get("allow_actions"))


def client_tools_available(agent_settings: Any, conversation_attributes: Any) -> tuple[bool, bool]:
    """(offer client tools?, allow mutating ops?) for one conversation.

    Consent is per conversation and lives on `conversation.attributes` where the
    widget wrote it, so it survives a page reload and a worker restart, and a
    visitor who never opted in never has their page touched.
    """
    if not page_control_enabled(agent_settings):
        return False, False
    attributes = conversation_attributes if isinstance(conversation_attributes, dict) else {}
    consent = attributes.get("page_control_consent")
    if consent is False:
        return False, False
    mutating = actions_allowed(agent_settings) and consent is True
    return True, mutating


def op_for(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Translate a tool call into the wire op the widget executes.

    Argument names are normalized here rather than in the widget: the model's
    vocabulary is a prompt-level concern, and the widget should only ever see one
    shape per op.
    """
    args: dict[str, Any] = {}
    if name == PAGE_SNAPSHOT:
        if tool_input.get("offset") is not None:
            args["offset"] = _int(tool_input["offset"], 0)
        return {"op": "snapshot", "args": args}
    if name == PAGE_FIND:
        args["query"] = str(tool_input.get("query") or "")
        if tool_input.get("limit") is not None:
            args["limit"] = _int(tool_input["limit"], 10)
        return {"op": "find", "args": args}
    if name == PAGE_READ:
        if tool_input.get("max_chars") is not None:
            args["max_chars"] = _int(tool_input["max_chars"], 6000)
        return {"op": "read", "args": args}
    if name == PAGE_ACT:
        args["index"] = _int(tool_input.get("index"), -1)
        kind = str(tool_input.get("kind") or "click")
        args["kind"] = kind if kind in ACT_KINDS else "click"
        if tool_input.get("text") is not None:
            args["text"] = str(tool_input["text"])
        if tool_input.get("submit") is not None:
            args["submit"] = bool(tool_input["submit"])
        return {"op": "act", "args": args}
    if name == PAGE_NAVIGATE:
        return {"op": "navigate", "args": {"url": str(tool_input.get("url") or "")}}
    if name == PAGE_SCROLL:
        direction = str(tool_input.get("dir") or "down")
        args["dir"] = direction if direction in ("up", "down") else "down"
        if tool_input.get("amount") is not None:
            args["amount"] = _int(tool_input["amount"], 600)
        return {"op": "scroll", "args": args}
    if name == PAGE_WAIT:
        if tool_input.get("ms") is not None:
            args["ms"] = _int(tool_input["ms"], 500)
        return {"op": "wait", "args": args}
    if name == SHOW_GUIDE:
        return {"op": "guide", "args": {"tour_id": str(tool_input.get("tour_id") or "")}}
    if name == SHOW_STEPS:
        return {
            "op": "steps",
            "args": {
                "title": str(tool_input.get("title") or ""),
                "steps": _steps(tool_input.get("steps")),
            },
        }
    raise ValueError(f"not a client tool: {name}")


def validate(name: str, tool_input: dict[str, Any]) -> str | None:
    """Reject a malformed call before it reaches the visitor's browser.

    A bad call answered here becomes a tool error the model can fix on the next
    turn; sending it on would spend a whole widget round-trip to learn the same
    thing.
    """
    if name == PAGE_FIND and not str(tool_input.get("query") or "").strip():
        return "query is required"
    if name == PAGE_ACT:
        index = tool_input.get("index")
        if index is None:
            return "index is required — take a page_snapshot and use an element's [index]"
        if _int(index, -1) < 0:
            return "index must be a non-negative integer from a page_snapshot"
        kind = str(tool_input.get("kind") or "click")
        if kind in ("fill", "select") and tool_input.get("text") is None:
            return f"kind={kind} needs text"
    if name == PAGE_NAVIGATE and not str(tool_input.get("url") or "").strip():
        return "url is required"
    if name == SHOW_GUIDE and not str(tool_input.get("tour_id") or "").strip():
        return "tour_id is required — call find_guide first"
    if name == SHOW_STEPS:
        steps = _steps(tool_input.get("steps"))
        if not steps:
            return "steps must be a non-empty array of {index?, title, body?}"
        if len(steps) > 8:
            return "at most 8 steps — split a longer walkthrough"
    return None


def _steps(value: Any) -> list[dict[str, Any]]:
    """Keep only well-formed steps; a step needs at least a title to be shown."""
    if not isinstance(value, list):
        return []
    steps: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        step: dict[str, Any] = {"title": title[:200]}
        if item.get("body") is not None:
            step["body"] = str(item["body"])[:1000]
        if item.get("index") is not None:
            step["index"] = _int(item["index"], -1)
        steps.append(step)
    return steps


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
