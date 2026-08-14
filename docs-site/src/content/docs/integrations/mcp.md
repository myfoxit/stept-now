---
title: MCP (AI clients)
description: Connect Claude Code, Claude Desktop, Cursor or any MCP client to your Stept workspace — knowledge, tours, conversations and real browser driving.
---

Stept ships a built-in [MCP](https://modelcontextprotocol.io) server at
`https://<your-stept-host>/mcp` (streamable HTTP). Any MCP client can search your
knowledge base, ask questions with citations, read conversations and tours — and, with
the Chrome extension connected, see and drive a real browser.

## Authentication

Auth is a workspace API key (`sk_stept_…`) sent as a Bearer token. Create keys in
**Settings → MCP · AI clients** — the full key is shown exactly once. Scopes:

- `read` — search/ask/read tools
- `write` — read + notes, document creation, browser driving
- `admin` — everything except workspace deletion

Requests are rate limited **per key**: 120/minute by default, configurable on self-hosted
instances via `STEPT_MCP_RATE_LIMIT_PER_MINUTE` (`0` disables — see
[Configuration](/reference/configuration/)).

## Connect a client

```bash
claude mcp add --transport http stept https://<host>/mcp \
  --header "Authorization: Bearer sk_stept_…"
```

Claude Desktop / Cursor config:

```json
{
  "mcpServers": {
    "stept": {
      "url": "https://<host>/mcp",
      "headers": { "Authorization": "Bearer sk_stept_…" }
    }
  }
}
```

Smoke test:

```bash
curl -X POST https://<host>/mcp \
  -H 'Authorization: Bearer sk_stept_…' -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

For clients without HTTP transport, run stdio mode from `backend/` with the key in the
environment:

```bash
STEPT_API_KEY=sk_stept_… uv run python -m app.mcp_stdio
```

## Tools

The workspace endpoint exposes 29 tools:

| Area | Tools |
| ---- | ----- |
| Knowledge & RAG | `search_knowledge`, `ask_knowledge_base` (answer + citations + confidence), `get_document`, `create_document` |
| Help center | `search_articles`, `get_article` |
| Tours | `list_tours`, `get_tour_steps`, `tours_health` (breakage rollup) |
| Inbox | `search_conversations`, `get_conversation`, `add_conversation_note` |
| Browser | `browser_list`, `browser_open`, `browser_snapshot`, `browser_act`, `browser_navigate`, `browser_scroll`, `browser_key`, `browser_find`, `browser_wait_for` (`selector?`, `text?`, `timeout_s`), `browser_page_text`, `browser_console`, `browser_network`, `browser_extract`, `browser_close`, `browser_record_start` / `browser_record_stop` (records a tour), `browser_run_tour` |

## Driving a real browser

The `browser_*` tools operate the user's real, logged-in Chrome through the Stept
extension: install it, sign in, and leave **Let Stept control this browser** enabled — it
keeps an outbound connection to the server. A client can then open pages, read indexed
snapshots (`[3]<button "Save">`), click and type with trusted input, wait for an element
or text with `browser_wait_for`, watch console and network, record a workflow as a tour,
or replay one with `browser_run_tour` (waits for the result — useful to verify a tour
still passes). Browser tools require the `write` scope and a connected extension; password
fields are never typed into or read.

- **Targeting** — `browser_act` takes a snapshot index, or an accessible-name target
  (`role` + `name`, e.g. `role: "button", name: "Save"`).
- **Screenshots are opt-in** — pass `include_screenshot` and the tool returns a real MCP
  image block alongside the text, so vision-capable clients see the page.
- **URL hygiene** — `browser_open` and `browser_navigate` accept public `http(s)` URLs
  only (no `file:`, no localhost/private ranges).

## Per-agent endpoint

Each AI agent can also be its own MCP endpoint:

```
https://<host>/mcp/agents/<agent-id>
```

Enable it on the agent's **MCP channel** card. Clients get `ask_agent` (grounded answers
using that agent's model, prompt and retrieval settings) plus only the tools explicitly
enabled on the agent — such as `search_knowledge`, `find_guide` and its custom actions.
Page-control tools are never exposed here.

Write tools honor the card's **approval mode**: `ask_in_chat` (default — the client
prompts its user), `ask_in_stept` (calls pause until approved on the Approvals page),
`never_ask`, or `deny`. Keys minted on the card are bound to that agent and are rejected
everywhere else — including the workspace `/mcp` endpoint and the REST API.
