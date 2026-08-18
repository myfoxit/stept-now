"""Standard MCP tool annotation sets.

Clients use these hints to decide what to confirm with the user before running:
a `readOnlyHint` tool can run freely, a `destructiveHint` one should prompt.
Every Stept tool acts on the caller's own workspace — never an unbounded
external world — so ``open_world_hint`` is always false. The one exception
would be the ``browser_*`` tools, which reach whatever page the user is on;
they are annotated at their own call sites.

Write tools pick their set from the verb prefix rather than restating it per
tool, so a new ``delete_thing`` cannot be born under-annotated.
"""

from __future__ import annotations

import re

from mcp.types import ToolAnnotations

#: Read tools: never mutate state.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

#: Additive write (create / duplicate): each call adds a new resource.
WRITE_CREATE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)

#: Idempotent write (update / publish / pause): re-running with the same args
#: converges to the same state.
WRITE_UPDATE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

#: Destructive write (delete / archive): removes existing data — clients should
#: confirm first.
WRITE_DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)

_DESTRUCTIVE_PREFIX = re.compile(r"^(delete_|remove_|archive_)")
_IDEMPOTENT_PREFIX = re.compile(r"^(update_|upsert_|publish_|pause_|restore_|set_|add_)")


def write_annotations_for(name: str) -> ToolAnnotations:
    """Annotation set for a write tool, chosen from its verb prefix.

    Unknown prefixes fall back to additive create — the safe, non-destructive
    default (a client that treats an unrecognised tool as harmless is wrong in
    the recoverable direction).
    """
    if _DESTRUCTIVE_PREFIX.match(name):
        return WRITE_DESTRUCTIVE
    if _IDEMPOTENT_PREFIX.match(name):
        return WRITE_UPDATE
    return WRITE_CREATE
