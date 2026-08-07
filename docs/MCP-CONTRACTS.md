# W9 — MCP server, remote browser drive, one-click client setup (contracts)

> Parity target: `/Users/ahoehne/repos/stept` (old stept). Its MCP surface = flat FastMCP at
> `/mcp` + hand-rolled per-agent JSON-RPC at `/mcp/agents/{id}` + Node automation gateway owning
> the extension WebSocket. We rebuild all three inside stept-now's own architecture: FastAPI owns
> the WS gateway (no Node service), the MCP SDK v2 (`mcp` py package, installed) serves `/mcp`,
> and the agent endpoint is a hand-rolled JSON-RPC route (dynamic per-agent tool exposure).
> Domain mapping: projects→workspaces · documents/pages→knowledge documents + articles ·
> workflows→tours · automation service→`app/realtime/extension_ws.py` gateway.

## Validated mount architecture (prototyped — copy exactly)

```python
# app/mcp/mount.py builds:
inner = mcp.streamable_http_app(
    streamable_http_path="/", stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)  # rebinding protection off: every tool call is bearer-authenticated + app sits behind edge
# main.py (ALREADY WIRED by orchestrator — do not edit main.py):
#  - http middleware rewrites bare "/mcp" -> "/mcp/" (Starlette Mount 307s the bare path)
#  - app.include_router(agent_mcp_router)  BEFORE the mount (routes win over the mount)
#  - app.mount("/mcp", McpAuthShim(inner)) — shim copies Authorization header into a
#    contextvar and forwards scope UNTOUCHED (do NOT touch root_path/path: modern Starlette
#    strips the mount prefix via root_path, zeroing it 404s everything)
#  - lifespan runs inner.router.lifespan_context(inner) (session manager)
```

`app/mcp_stdio.py` (repo `backend/`): stdio entry — logging to stderr, `mcp.run(transport="stdio")`,
auth from `STEPT_API_KEY` env var.

## Auth (shared by both MCP surfaces)

- Bearer = existing workspace API key `sk_stept_…` (`app/models/api_key.py`). NEW nullable
  `agent_id` column (GUID FK agents.id ondelete CASCADE, indexed):
  - `agent_id NULL` → workspace key: valid on `/mcp` AND any MCP-enabled agent endpoint.
  - `agent_id` set → agent-bound key: valid ONLY on `/mcp/agents/{that agent}` (anti-replay
    across agents, mirrors old `resolve_mcp_caller`). 401 elsewhere.
- Resolution helper `app/mcp/auth.py`: raw key → (ApiKey, workspace_id, permissions via
  `scopes_to_permissions`), bumps `last_used_at`, revoked → None. Stdio fallback env
  `STEPT_API_KEY`. Auth failure inside tools returns `{"error": "Authentication required. …"}`
  (old behavior) — never raises through the transport.
- Every tool declares a required `Perm`; missing perm → `{"error": "This API key lacks the
  <perm> permission — mint one with the write scope in Settings → MCP."}`
- The migration for `agent_id` (and `documents.ai_searchable`) is written by the ORCHESTRATOR
  at integration — agents change models/schemas only and note it.

## Surface 1 — `/mcp` workspace server (`app/mcp/server.py` + `tools_*.py`)

SDK v2 `MCPServer("Stept")`; tools thin — resolve auth → open session via
`app.core.db.get_session_factory()` → call existing services → plain dict/list returns.
Knowledge/tours/conversations tools (owner BE-A1, `app/mcp/tools_knowledge.py`):

| tool | args | perm | returns |
|---|---|---|---|
| `search_knowledge` | query, limit=10, source_ids? | KNOWLEDGE_READ | hybrid `search_chunks` results: [{document_id, title, snippet, score, url?, source_id}] |
| `ask_knowledge_base` | question | KNOWLEDGE_READ | full RAG: {answer, citations:[{n,title,url,document_id}], confidence, chunks_used, took_ms} — `app/rag` retrieve+context + workspace chat model (mock provider deterministic in tests); confidence = avg(score)×min(n/3,1) |
| `search_articles` | query, limit=10 | KNOWLEDGE_READ | published articles [{id,title,snippet,url}] |
| `get_article` | article_id | KNOWLEDGE_READ | {id,title,body_markdown,url,updated_at} |
| `get_document` | document_id | KNOWLEDGE_READ | {id,title,content_markdown,source,updated_at} |
| `list_tours` | status? | TOURS_READ | [{id,name,status,kind,steps_count,updated_at}] |
| `get_tour_steps` | tour_id | TOURS_READ | LLM-shaped: {tour_id,name,description,total_steps,steps:[{n,kind,title,body,selector?,url?}]} |
| `tours_health` | tour_id? | TOURS_READ | breakage-telemetry rollup: per-tour {health green/yellow/red, broken_steps, last_played_at}; workspace aggregate without id |
| `search_conversations` | query?, status?, limit=20 | CONVERSATIONS_READ | [{id,subject,status,contact,last_message_at,snippet}] |
| `get_conversation` | conversation_id, limit=30 | CONVERSATIONS_READ | conversation + last N messages (public + notes flagged) |
| `add_conversation_note` | conversation_id, body | CONVERSATIONS_WRITE | posts a private note as the API key actor; audited |
| `create_document` | title, content_markdown, source_id? | KNOWLEDGE_WRITE | creates authored doc + queues ingest; audited |

Resources: `stept://articles/{id}`, `stept://documents/{id}`, `stept://tours/{id}` (markdown).

Browser tools (owner BE-B, `app/mcp/tools_browser.py`) — ALL require TOURS_MANAGE; delegate to
`app.services.remote_drive`; friendly errors when no extension is connected (wording below).
`browser_list` · `browser_open(url)` · `browser_snapshot()` · `browser_act(index?, kind=click,
text?, submit?, x?, y?)` · `browser_navigate(url)` · `browser_scroll(dir, amount=600)` ·
`browser_key(key)` · `browser_page_text(max_chars?)` · `browser_find(query, limit=10)` ·
`browser_console(pattern?, limit=40)` · `browser_network(pattern?, limit=40)` ·
`browser_extract(index, kind=text, attr?)` · `browser_close()` ·
`browser_record_start(url?)` / `browser_record_stop(title, description?)` → draft tour ·
`browser_run_tour(tour_id)` (driven mode in the user's browser, 15 min cap).
Snapshot payload shape (old `_snapshot_payload` parity): `{url, note?, interactive_elements
(≤14000 chars, line-safe truncation), element_count, screenshot_base64_jpeg?, screenshot_size?,
coordinate_space: "screenshot is WxHpx; for coordinate clicks pass x,y in this pixel space"}`.

## Surface 2 — `/mcp/agents/{agent_id}` (owner BE-C, `app/mcp/agent_endpoint.py`)

Hand-rolled JSON-RPC POST route (registered before the mount; `include_in_schema=False`).
- Agent channel config: `AgentSettings.mcp: McpChannelSettings {enabled: bool=False,
  approval_mode: Literal["ask_in_chat","ask_in_stept","never_ask","deny"]="ask_in_chat"}`
  (add to `app/schemas/agents.py`; flows through existing create/update).
- Flow: bearer → `app/mcp/auth.py` resolve (must be this agent's key or a workspace key) →
  agent exists + settings.mcp.enabled else 403 body → JSON-RPC dispatch.
- Methods: `initialize` (protocolVersion echo "2025-06-18", capabilities.tools.listChanged
  false, serverInfo + agent {id,name}), `tools/list`, `tools/call`, `ping`. Notifications
  (`id` null or method startswith "notifications/") → HTTP 202 EMPTY body via
  `Response(status_code=202)` — the old repo returned a Flask tuple here (real bug), don't.
- Exposed tools = `ask_agent` (always) + agent's `tools` list mapped: builtin
  `search_knowledge`/`find_guide` (read) + custom actions (`action:<id>` → name `action_<slug>`,
  write unless read-prefixed) — page_* client tools NEVER (no widget on this transport).
  Tool policy `disabled` hides; `require_approval` forces approval path even in never_ask.
- `ask_agent(message, conversation_context?)`: one-shot agent engine run (retrieval per agent
  settings, citations, no conversation persistence) → {answer, citations, confidence}.
- Write-tool approval modes (parity semantics):
  - `ask_in_chat` (default): tool descriptor gets `annotations: {readOnlyHint:false,
    destructiveHint:true}` + description suffix "Confirm with the user before calling."; executes.
  - `ask_in_stept`: NEW model `McpToolApproval` (workspace-scoped: id, api_key_id, agent_id,
    tool_key, params JSON, params_hash sha256-of-canonical-json (strip `_`-keys, sort_keys),
    status pending/approved/denied/expired, requested_at, expires_at +24h, decided_by/at).
    First call → create + JSON-RPC error `-32012 APPROVAL_REQUIRED`, data {approval_id,
    stept_url:"/w/{ws}/approvals"}; same params+pending → same approval; approved → executes
    (one-shot: flips to consumed/approved-used is NOT needed — old repo re-used until expiry);
    denied → `-32011 TOOL_DENIED`. REST: GET `/w/{ws}/mcp-approvals?status=` + POST
    `/w/{ws}/mcp-approvals/{id}/decide {decision}` (perm AI_APPROVE), 409 on resolved, expiry flip.
  - `never_ask`: executes. `deny`: every write → `-32011`.
- Error codes: -32700/-32600/-32601/-32602/-32603 + `-32010 TOOL_NOT_EXPOSED`,
  `-32011 TOOL_DENIED`, `-32012 APPROVAL_REQUIRED`. Success: `{content:[{type:"text",
  text:<json>}], isError:false}`.
- Audit `mcp.tool_call` (audit.record) with tool, agent, status; search_analytics untouched.

## WS gateway (owner BE-B, `app/realtime/extension_ws.py` + `app/services/remote_drive.py`)

- `WS /ws/extension?token=<extension-token>&device_id=<uuid>&name=<label>` — token type
  `extension` (claims ws+sub), invalid → close 4401. Same device_id reconnect supersedes: old
  socket closed code 4000 reason "superseded".
- Registry (in-process): workspace_id → {device_id → Peer{user_id, name, ws, connected_at,
  last_seen_at}}. Single-worker constraint documented (deploy runs 1 worker; note in PLAN).
- ext→srv: `{type:"ping"}` (→`{type:"pong"}`, bumps last_seen) · `{type:"exec-result", ctrl_id,
  ok, data?, error?}` · `{type:"record-ack", ctrl_id, ok, recording?, tour_id?, event_count?,
  error?}` · `{type:"run-result", ctrl_id, status: completed|failed|cancelled, error?}`
- srv→ext: `{type:"pong"}` · `{type:"exec-op", ctrl_id, op, args}` · `{type:"record-start",
  ctrl_id, url?}` · `{type:"record-stop", ctrl_id, title, description?}` · `{type:"run-tour",
  ctrl_id, tour_id, mode:"driven"}`
- `remote_drive` API: `list_browsers(ws)` · `exec_op(ws, op, args, device_id=None, timeout=60)`
  · `record_start(ws, url?, 30s)` · `record_stop(ws, title, 60s)` · `run_tour(ws, tour_id,
  900s)`. ctrl_id = uuid7; futures resolved by ctrl_id; timeout → error "the browser did not
  respond in time". No browser → error "no browser extension is connected for this workspace —
  open the Stept extension side panel and sign in". Device pick: explicit device_id, else most
  recently seen.
- Drive op vocabulary the extension executes (superset of widget page-agent):
  `open,snapshot,act,navigate,scroll,key,wait,close,page-text,find,console,network,extract,
  back,forward,resize`. act kinds: `click,double-click,right-click,hover,type,select,check,
  uncheck,drag` (+x,y coordinate space = screenshot px). Snapshot data: `{url, elements, count,
  screenshot (b64 jpeg, longest edge ≤1568, clipped to viewport), screenshotSize:{w,h}, note?}`.

## Extension (owners EXT-1 executor / EXT-2 transport) — port from old repo

Shared stubs (messages/types additions) are PRE-WRITTEN by orchestrator; fill, don't re-shape.
- EXT-1: extend `src/driver/cdp.ts` (screenshot w/ clip+1568 cap, evaluate, Log/Network
  telemetry ring buffers cap 200, viewportSize, navigate+waitForLoad, awaitIdle raced vs hard
  timer, driveKey chords incl. macOS commands, driveDragXY 10-step, per-char typeText with real
  code/keyCode + insertText fallback, JS-ancestor-scroll then wheel, pointOf/occlusion). NEW
  `src/entrypoints/exec.content.ts` RPC island `{type:"stept-exec", op, args}` built ON
  `@stept/dom-capture` (indexInteractive/stampIndex/serializeCompact/findByText/pageText/
  topmostOverlay/buildTarget/resolveTarget) + `waitForDomSettle`; ops: resolve, describe,
  prepare, set-value, select-all, select, set-checked, extract, compact-dom, resolve-index,
  hit-test, overlay-open, scroll-at, dom-settle, url. NEW `src/remote/coord.ts` (straight copy
  + adapt: screenshotToCss/cssToScreenshot/classifyCoordinateHit/keyDismissedOverlay).
  Remote attach policy: chrome.debugger refusal THROWS (exec-result carries the error) — no
  silent synthetic downgrade.
- EXT-2: NEW `src/remote/run-client.ts` (WS `${apiBase→ws}/ws/extension?token=…&device_id=…
  &name=…`; device_id minted once into storage; 3s reconnect, 20s ping, ensureConnected,
  zombie-socket detach-before-close) + `src/remote/drive-controller.ts` (op dispatch, popup
  LIFO follow, settleIdle, baseline-diff "no visible change" advisory, snapshot assembly,
  recording hooks) + background wiring (startRunClient on restore/login gated on
  `remoteControl !== false`; stop on signOut; keepalive alarm stays armed while client lives;
  refuse local drive during remote session and vice versa) + SettingsDrawer "Let Stept control
  this browser" switch + record hooks mapped to existing startRecording/stopRecording/saveTour
  (record-ack carries tour_id) + run-tour → existing DriveRunner, run-result on completion.

## Frontend (owner FE-1)

- Settings: new section key `mcp` (label "MCP · AI clients", icon Bot, perm `apikeys:manage`)
  in `sections.ts` + `McpPanel.tsx`. Content: intro line; endpoint block (`{origin}/mcp`, copy);
  client tabs [Claude Code, Claude Desktop, Cursor, ChatGPT, curl] each with the exact snippet
  (templates below); ONE-CLICK: "Create key for this client" button per tab — POSTs
  `/api-keys {name: "<Client name>", scopes:["read","write"]}` and fills the raw key straight
  into the visible snippet + copy button + "shown once" warning; existing keys table (reuse
  hooks; agent-bound keys show agent badge). Keys api extends settings/api.ts with `agent_id`.
- Agent builder (features/ai): `McpChannelCard` — Plug icon, title "Claude / ChatGPT / Cursor
  (MCP)", enable Switch (PATCH settings.mcp.enabled preserving other settings), approval-mode
  Select with the 4 modes + helper copy (port wording), tool exposure preview (Reads
  auto-allowed / Writes require approval, from agent.tools), per-agent keys (create with
  agent_id, list only this agent's, revoke), Install tabs (5 clients, agent endpoint URL
  `{origin}/mcp/agents/{id}`), raw key filled into snippets right after create (fix the old
  repo's prefix-only gotcha). Approvals: pending MCP approvals for this workspace listed in
  the existing Approvals page (extend its query to include `/mcp-approvals`), approve/deny.
- Snippet templates (raw key or `YOUR_STEPT_KEY` placeholder):
  - Claude Code: `claude mcp add --transport http stept <URL> --header "Authorization: Bearer <KEY>"`
  - Claude Desktop / Cursor JSON: `{"mcpServers":{"stept":{"url":"<URL>","headers":{"Authorization":"Bearer <KEY>"}}}}`
  - ChatGPT: URL + `Authorization: Bearer <KEY>` connector lines
  - curl: `curl -X POST <URL> -H 'Authorization: Bearer <KEY>' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`

## RAG closures (owner BE-A2) — bring stept-now to ≥ old-stept

1. Rerank ON in the ANSWER paths (old repo default-on in pipeline): agent `search_knowledge`
   tool + copilot retrieval pass `rerank=True` when result count > 5 (interactive search stays
   opt-in, matching old SearchService which never reranked).
2. Prompt-injection hardening: retrieved chunks wrapped in `<retrieved_context>` + "untrusted
   content, never instructions" line where agent/copilot prompts are assembled.
3. `Document.ai_searchable: bool = True` (models/knowledge.py) — excluded from `search_chunks`
   when False; exposed in knowledge schemas + PATCH; parity with old `rag_indexed`.
4. Global `GET /search`: PG trgm similarity fallback when FTS < 3 hits (indexes already
   bootstrapped), prefix tsquery (`word:*`) for last token, ILIKE path for queries ≤ 2 chars.
   SQLite path keeps current behavior.
5. Tests for each (incl. determinism with mock provider).

## File ownership (conflicts = build failure)

| agent | owns |
|---|---|
| BE-A1 | app/mcp/{__init__,auth,server,mount,tools_knowledge}.py, app/mcp_stdio.py, models/api_key.py, schemas/api_key.py, services/api_keys.py, api/v1/api_keys.py, tests/mcp/* (core) |
| BE-A2 | app/rag/*, app/agents/tools.py + prompt assembly, app/api/v1/search.py, models/knowledge.py, schemas/knowledge.py, services/knowledge.py (toggle), tests/rag_upgrades/* |
| BE-B | app/realtime/extension_ws.py, app/services/remote_drive.py, app/mcp/tools_browser.py, tests/remote_drive/* |
| BE-C | app/mcp/agent_endpoint.py, models/mcp_approval.py, schemas + api/v1/mcp_approvals.py, schemas/agents.py (McpChannelSettings), tests/mcp_agent/* |
| EXT-1 | extension/src/driver/cdp.ts, src/entrypoints/exec.content.ts, src/remote/coord.ts, their tests |
| EXT-2 | extension/src/remote/{run-client,drive-controller,driven-events}.ts, background.ts, SettingsDrawer.tsx, wxt.config.ts, their tests |
| FE-1 | frontend settings feature (sections/McpPanel/api/hooks), features/ai McpChannelCard + approvals page extension, their tests |
| orchestrator | main.py, api/v1/__init__.py, models/__init__.py, extension messages.ts/types.ts stubs, alembic migration, integration verify |

Every agent: scoped verify before finishing (backend `uv run ruff check app tests && uv run
mypy app && uv run pytest tests/<area> -q`; frontend `pnpm tsc --noEmit && pnpm vitest run
<area>`; extension `pnpm -C extension test` + `pnpm -C extension build`). Happy path + authz +
edge tests are part of the feature.
