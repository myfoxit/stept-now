# Connecting AI agents over MCP

Stept ships a built-in [MCP](https://modelcontextprotocol.io) server, so Claude Code, Claude
Desktop, Cursor, ChatGPT or any MCP client can search your knowledge base, ask questions with
citations, read conversations and tours — and, with the Chrome extension connected, see and
drive a real browser.

## 1. Create a key (Settings → MCP · AI clients)

Pick your client's tab and press **Create key for this client** — the key is created with a
sensible name and the setup snippet below it is filled in, ready to copy. The full key is shown
exactly once.

Keys are ordinary workspace API keys (`sk_stept_…`). Scopes map to what the MCP tools may do:
`read` → search/ask/read tools, `write` → notes + document creation + browser driving,
`admin` → everything except workspace deletion.

Requests are rate limited **per key**: 120/minute by default, configurable via
`STEPT_MCP_RATE_LIMIT_PER_MINUTE` (`0` disables).

## 2. Point your client at the server

Endpoint: `https://<your-stept-host>/mcp` (streamable HTTP).

- **Claude Code**
  ```bash
  claude mcp add --transport http stept https://<host>/mcp \
    --header "Authorization: Bearer sk_stept_…"
  ```
- **Claude Desktop / Cursor** — add to the client's MCP config:
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
- **ChatGPT** — Settings → Connectors → Add: URL as above, auth header
  `Authorization: Bearer sk_stept_…`.
- **Anything else / smoke test**
  ```bash
  curl -X POST https://<host>/mcp \
    -H 'Authorization: Bearer sk_stept_…' -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
  ```
- **stdio** (for clients without HTTP support), from `backend/`:
  ```bash
  STEPT_API_KEY=sk_stept_… uv run python -m app.mcp_stdio
  ```

## 3. What the tools can do

50 tools:

| Area | Tools |
|---|---|
| Knowledge & RAG | `search_knowledge`, `ask_knowledge_base` (answer + citations + confidence), `get_document`, `create_document` |
| Help center | `search_articles`, `get_article` |
| Authoring | `get_authoring_guide`, `get_experience_schema`, `create_tour`/`update_tour`/`publish_tour`/`pause_tour`, the same four for `checklist` and `survey`, `validate_experience` |
| Adoption analytics | `get_adoption_overview` (everything ranked by reach), `get_tour_analytics` (funnel + biggest drop-off + playback health), `get_checklist_analytics`, `get_survey_results` |
| Diagnosis | `diagnose_experience` ("why isn't this showing?"), `diagnose_contact` ("what would this visitor see?") |
| Tours (DAP) | `list_tours`, `get_tour_steps`, `tours_health` (self-healing/breakage rollup) |
| Inbox | `search_conversations`, `get_conversation`, `add_conversation_note` |
| Browser (via extension) | `browser_list`, `browser_open`, `browser_snapshot`, `browser_act`, `browser_navigate`, `browser_scroll`, `browser_key`, `browser_find`, `browser_wait_for` (`selector?`, `text?`, `timeout_s`), `browser_page_text`, `browser_console`, `browser_network`, `browser_extract`, `browser_close`, `browser_record_start`/`browser_record_stop` (records a tour), `browser_run_tour` |

### Building onboarding with an AI assistant

The server's `initialize` instructions carry a routing map, so a connected client knows the
order without being told. The path that works:

1. **`get_authoring_guide`** — call it with no arguments for the lifecycle + publish contract
   and a table of contents, then fetch the sections for your content type in one call
   (`section` takes an array, e.g. `["tour-steps", "targets", "targeting"]`). Authoring from
   guesswork produces content that publishes green and never renders; this is the contract that
   prevents it.
2. **`get_experience_schema("tour"|"checklist"|"survey")`** when a field shape is unclear — it
   returns the exact JSON Schema the create/update tools validate against.
3. **Record instead of guessing selectors.** With the extension connected, `browser_open` →
   `browser_record_start` → drive the flow with `browser_act` → `browser_record_stop` captures
   real selectors, healing descriptors and per-step URLs. Hand-written selectors are the
   fallback, not the default.
4. **`create_*`** — everything lands as a **draft** and reaches nobody until published.
5. **`validate_experience`** — the dry run. Returns `{ok, errors, warnings}`; errors mean the
   content cannot render or cannot be reached, warnings mean it renders but maybe not to whom
   you intended.
6. **`publish_*`**, then **`diagnose_experience`** if it still does not appear.

Two things worth knowing before the first call: `steps` / `items` / `questions` are **full
replacements** on update (an entry you omit is deleted), and a `manual` trigger is never
auto-delivered — it only runs when something asks for it by id.

### Reading the results

`get_adoption_overview` ranks every tour, checklist and survey by reach in one call — start
there, then deep-dive. `get_tour_analytics` returns the step funnel already differenced into
per-step drop-off with `biggest_drop_off` naming the worst step, plus playback health, because
a poor completion rate caused by a broken selector needs a different fix from one caused by
bad copy. Analytics need only `reports:read`, so an analyst's key cannot edit anything.

## 4. Driving a real browser

Install the Stept Chrome extension and sign in. It keeps an outbound connection to
`/ws/extension`; the **Let Stept control this browser** switch in the extension's settings
drawer gates the whole capability (on by default while signed in, off kills the connection).

A connected AI agent can then `browser_open` a page, read an indexed snapshot
(`[3]<button "Save">` …), click/type/scroll with trusted input, wait for an element or text
with `browser_wait_for`, watch console/network, record a workflow as a tour, or replay an
existing tour — in the user's real, logged-in browser. Browser tools require a key with the
`write` scope. Chrome shows its debugging banner while a drive session is attached; password
fields are never typed into or read.

- `browser_act` targets a snapshot index, or an accessible name via `role` + `name`
  (e.g. `role: "button", name: "Save"`).
- Screenshots are opt-in: pass `include_screenshot` and the tool returns a real MCP image
  block alongside the text.
- `browser_open` / `browser_navigate` accept public `http(s)` URLs only (no `file:`, no
  localhost/private ranges).

## 5. Exposing a single AI agent as a channel

Each configured Stept agent can also be its own MCP endpoint:
`https://<host>/mcp/agents/<agent-id>` — enable it on the agent's **MCP channel** card. External
LLM clients get `ask_agent` (grounded answers with citations, using the agent's model, prompt
and retrieval settings) plus the agent's own enabled tools.

Write tools honor the card's **approval mode**: ask in chat (default — the client prompts its
user), ask in Stept (calls pause until approved on the Approvals page), never ask, or deny.
Keys minted on the card are bound to that agent and don't work anywhere else — the workspace
`/mcp` endpoint and the REST API both reject them. Agent-bound keys are MCP-only.
