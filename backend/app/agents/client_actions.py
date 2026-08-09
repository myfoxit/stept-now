"""Developer-registered client actions: the host app's own verbs.

`page_*` (app.agents.page_tools) lets the agent act on the page's DOM. A client
action is one level up: the HOST PAGE registers a named function
(``Stept('action', {...})``), the widget advertises the definitions alongside
its page context, and the agent may call them like any other tool. Execution is
deferred to the browser through the same park/resume machinery as a page op —
the handler runs in the page, with the signed-in user's cookies and
permissions. This server never calls the customer's API.

Trust model: the definitions come from code the customer's developer shipped on
their own page, and that is what authorizes offering them at all — there is no
per-visitor DOM consent because nothing here touches the DOM. What keeps a run
honest anyway:

- ``confirm`` (default ON) puts a card in the thread before anything executes;
- ``approval`` routes the call through the existing durable TEAM approval gate;
- ``requires_identity`` withholds an action from anonymous visitors;
- a per-run cap, schema validation, and the full AgentStep trace.

Defs are stored on ``conversation.attributes["client_actions"]`` where the
widget's page-context POST put them — the same place page-control consent
lives, so they survive reloads and worker restarts without a table.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.ai.base import ToolSpec

#: LLM-visible names are ``app_<name>``: never collides with builtins or
#: ``page_*``, and a trace line reads as what it is — the customer's app verb.
SPEC_PREFIX = "app_"

#: Where the widget's page-context intake stores the block on
#: ``conversation.attributes``.
ATTR_KEY = "client_actions"

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

MAX_DEFS = 20
MAX_DESCRIPTION = 500
MAX_SCHEMA_CHARS = 4000
MAX_STORED_CHARS = 16_000

#: Per-run cap on ``app_*`` calls. An action loops through a human confirm card
#: at most, not through the visitor's patience: a model stuck retrying must run
#: out of rope, like MAX_MUTATING_OPS does for page ops.
MAX_ACTION_CALLS = 10


def is_action_name(tool_name: str) -> bool:
    """Is this LLM-visible tool name a client action?"""
    return tool_name.startswith(SPEC_PREFIX)


def spec_name(name: str) -> str:
    return f"{SPEC_PREFIX}{name}"


def normalize_defs(raw: Any) -> list[dict[str, Any]]:
    """Keep only well-formed defs, deduped by name (last registration wins).

    Dropping rather than erroring mirrors the rest of the widget surface: a
    hostile or buggy page must not be able to 422 its own conversation, and the
    page-context response echoes what WAS accepted so the SDK can warn about the
    rest. Order is preserved; the total stored payload is budgeted so a page
    cannot bloat every future agent run's tool list.
    """
    if not isinstance(raw, list):
        return []
    by_name: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        params = item.get("params")
        if params is not None:
            if not isinstance(params, dict):
                continue
            try:
                if len(json.dumps(params)) > MAX_SCHEMA_CHARS:
                    continue
            except (TypeError, ValueError):
                continue
        normalized: dict[str, Any] = {
            "name": name,
            "description": description[:MAX_DESCRIPTION],
            "confirm": bool(item.get("confirm", True)),
            "approval": bool(item.get("approval", False)),
            "requires_identity": bool(item.get("requires_identity", False)),
        }
        if params:
            normalized["params"] = params
        # A re-registration replaces def AND position: the page's latest word wins.
        by_name.pop(name, None)
        by_name[name] = normalized

    defs: list[dict[str, Any]] = []
    spent = 0
    for entry in by_name.values():
        if len(defs) >= MAX_DEFS:
            break
        cost = len(json.dumps(entry))
        if spent + cost > MAX_STORED_CHARS:
            break
        defs.append(entry)
        spent += cost
    return defs


def stored_block(defs: list[dict[str, Any]], *, identified: bool) -> dict[str, Any]:
    """The shape written to ``conversation.attributes[ATTR_KEY]``.

    ``identified`` is decided at intake time (the widget principal knows whether
    this contact came through a verified HMAC boot) because the resolver only
    has the conversation in hand.
    """
    return {"defs": defs, "identified": identified}


def stored_defs(conversation_attributes: Any) -> tuple[list[dict[str, Any]], bool]:
    """(defs, identified) as stored — tolerant of missing/legacy shapes."""
    attributes = conversation_attributes if isinstance(conversation_attributes, dict) else {}
    block = attributes.get(ATTR_KEY)
    if not isinstance(block, dict):
        return [], False
    defs = block.get("defs")
    return (
        [d for d in defs if isinstance(d, dict)] if isinstance(defs, list) else [],
        bool(block.get("identified")),
    )


def enabled(agent_settings: Any) -> bool:
    """May this agent use the page's registered actions? ON by default.

    Page control is opt-in because it acts on a DOM the developer never
    reviewed. A client action IS developer-reviewed code — registering it is
    the opt-in — so the agent-level switch exists only as an off-switch.
    """
    settings = agent_settings if isinstance(agent_settings, dict) else {}
    block = settings.get(ATTR_KEY)
    if isinstance(block, dict) and block.get("enabled") is False:
        return False
    return True


def offered(defs: list[dict[str, Any]], *, identified: bool) -> list[dict[str, Any]]:
    """The defs an agent run may see. ``requires_identity`` defs are WITHHELD
    (not refused) from anonymous visitors, so the model never proposes what it
    cannot do — the same guarantee page tools give unconsented mutations."""
    return [
        d
        for d in defs
        if isinstance(d.get("name"), str)
        and NAME_RE.match(str(d["name"]))
        and (identified or not d.get("requires_identity"))
    ]


def spec_for(action_def: dict[str, Any]) -> ToolSpec:
    schema = action_def.get("params")
    if not isinstance(schema, dict) or not schema:
        schema = {"type": "object", "properties": {}}
    return ToolSpec(
        name=spec_name(str(action_def["name"])),
        description=str(action_def.get("description") or ""),
        input_schema=schema,
    )


def op_for(action_def: dict[str, Any], tool_input: dict[str, Any]) -> dict[str, Any]:
    """The wire op the widget executes for one action call.

    ``confirm`` travels IN the op: the iframe decides whether to gate on a card,
    and it must not have to re-join against a defs list that may have changed
    since this call was parked.
    """
    return {
        "op": "action",
        "args": {
            "name": str(action_def.get("name") or ""),
            "params": tool_input,
            "confirm": bool(action_def.get("confirm", True)),
        },
    }


def validate(action_def: dict[str, Any], tool_input: dict[str, Any]) -> str | None:
    """Schema-check a call before it spends a browser round-trip."""
    from app.agents.tools import validate_params  # local: tools imports this module

    schema = action_def.get("params")
    return validate_params(schema if isinstance(schema, dict) else None, tool_input)
