"""The workspace MCP server instance.

Importing this module registers every tool: the ``tools_*`` modules attach
themselves to ``mcp`` via decorators at import time. Keep this module free of
tool bodies — it exists so ``app.main`` and ``app/mcp_stdio.py`` have one thing
to import.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

#: Delivered in the `initialize` handshake, so a client has the ROUTING MAP
#: before its first tool call. Division of labor: this stays a compact map (it
#: is paid for on every connection); the deep authoring contract — step rules,
#: target strategy, per-type publish requirements — lives in
#: ``get_authoring_guide`` and is fetched only when someone is authoring.
#:
#: Keep it a map, not a manual. Every line should prevent a specific wrong first
#: move: not reading the guide before authoring, hand-writing selectors when a
#: browser is connected, re-targeting blindly instead of diagnosing, assuming a
#: draft is live.
SERVER_INSTRUCTIONS = """\
You are connected to a Stept workspace — a customer-support and product-adoption \
platform. Support lives in conversations and a RAG knowledge base; adoption lives \
in tours, checklists and surveys delivered to end users by an embedded widget.

Route by intent:

- Answer a question / find something → `ask_knowledge_base` (cited answer), \
`search_knowledge` (raw chunks), `search_articles` / `get_article` (help center).
- Build or edit adoption content → call `get_authoring_guide` FIRST (no args \
returns the core contract plus a table of contents; then fetch the sections for \
your type in ONE call — `section` takes an array). Then `create_tour` / \
`create_checklist` / `create_survey` → `validate_experience` → `publish_*`. \
Content is created as a DRAFT and reaches nobody until it is published.
- Unsure of a field shape → `get_experience_schema(type)` returns the exact JSON \
Schema the create/update tools validate against. Do not guess step shapes.
- Author a tour against a REAL app → drive the user's Chrome instead of guessing \
selectors: `browser_open`, `browser_record_start`, walk the flow with \
`browser_act`, then `browser_record_stop`. That captures real selectors, healing \
descriptors and per-step URLs — hand-written selectors are the fallback, not the \
default. `browser_run_tour` replays one.
- "Why isn't my tour showing?" → `diagnose_experience`. It evaluates the same \
delivery gates the widget applies. Pass `url` and `contact_id` or gates come back \
`unknown`. Use it BEFORE changing targeting.
- "What would this customer see?" → `diagnose_contact(contact_id, url)` sorts \
every live experience into showing/blocked with the delivery cap applied.
- Measure → `get_adoption_overview` ranks all content by reach in one call; then \
`get_tour_analytics` / `get_checklist_analytics` / `get_survey_results` for the \
funnel, the friction point, or the verbatims.
- Support threads → `search_conversations`, `get_conversation`, \
`add_conversation_note`.

Facts that prevent common mistakes:

- A tour with `trigger.type = "manual"` is NEVER auto-delivered — it only runs \
when something asks for it by id. Choosing manual and then wondering why nothing \
appears is the most common authoring mistake.
- `steps`, `items` and `questions` are FULL REPLACEMENTS on update, not patches: \
an entry you omit is deleted. Omitting the field itself leaves it untouched.
- Banners and announcements are tours with a different `kind`, not separate types, \
and they render exactly one step.
- The widget delivers at most 5 experiences per page, ordered by priority — below \
that cutoff nothing renders.
- Audience filters on email / external_id / attributes.* can never match an \
anonymous visitor. If the host page does not call `Stept('identify', …)`, \
targeted content validates green, publishes green, and shows to nobody.
- The tool list is scope-gated by your API key: a tool you cannot see is outside \
this key's scopes, not a missing feature.
"""

mcp = MCPServer("Stept", instructions=SERVER_INSTRUCTIONS)

# Tool registration by import side effect — order is alphabetical, not meaningful.
from app.mcp import tools_analytics as _tools_analytics  # noqa: E402,F401
from app.mcp import tools_authoring as _tools_authoring  # noqa: E402,F401
from app.mcp import tools_browser as _tools_browser  # noqa: E402,F401
from app.mcp import tools_diagnose as _tools_diagnose  # noqa: E402,F401
from app.mcp import tools_knowledge as _tools_knowledge  # noqa: E402,F401
