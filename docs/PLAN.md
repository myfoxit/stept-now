# Stept — Build Plan & Status

> **Living document.** Updated at every wave boundary. If context was summarized/reset: read this file first, then `CLAUDE.md`, then `docs/CONTRACTS.md`.

## What we are building

**Stept** — a world-class open-source Intercom + Fin alternative:

- Shared team inbox (conversations, assignment, teams, notes, tags, canned responses, CSAT)
- Multi-channel: web chat widget, email, Slack, Telegram, public API channel
- Knowledge & RAG: upload/crawl sources → parse → chunk → embed → hybrid retrieval (pgvector + FTS, RRF fusion) with citations; public help center
- Multi-provider AI: OpenAI, Anthropic, Google, Ollama, any OpenAI-compatible, plus a deterministic **mock provider** (offline dev/tests/e2e)
- Agent engine ("Fin"): configurable AI agents with tools, guardrails, **approval gates** (human-in-the-loop pause/resume), full traces, copilot reply suggestions
- Automation rules, outbound webhooks, audit log, notifications, reports/analytics
- RBAC (builtin roles + custom roles), team management, invitations, API keys
- DAP: product tours built with a Chrome recorder extension, played by the widget loader in the host page
- Embeddable widget (`window.Stept` loader + iframe app), identity verification via HMAC
- World-class tests: pytest (backend), vitest (frontend), Playwright (e2e) — all runnable with **zero external services** (SQLite + in-memory pubsub/queue + mock AI), full-fidelity mode via docker compose (Postgres/pgvector + Redis + Mailpit)

## Environment facts

- Session worktree (ALL work happens here): `/Users/ahoehne/repos/stept-now/.claude/worktrees/build` — branch `worktree-build`; main checkout: `/Users/ahoehne/repos/stept-now` (branch `master`)
- Scratchpad (research clones): `/private/tmp/claude-501/-Users-ahoehne-repos-stept-now/418ccf97-660c-4f06-b9bd-eb697d851efe/scratchpad`
- Tooling: python3.12 + uv 0.10, node 25 + pnpm 10, docker 20.10 (old — check `docker compose` vs `docker-compose`), gh authed (`myfoxit`), **no git origin configured yet**
- **Port constraints:** old stept containers already occupy 8000/80/5173. Our ports: backend **8600**, frontend dev **5273**, Postgres **54329**, Redis **63790**, Mailpit **11025 (smtp) / 18025 (ui)**.

## Stack (locked decisions)

| Layer | Choice |
|---|---|
| Backend | FastAPI, SQLAlchemy 2 async, Pydantic v2, Python 3.12, uv |
| DB | Postgres 16 + pgvector (prod/dev via compose) **and** SQLite/aiosqlite (tests/zero-dep dev) — portable column types (GUID/JSON/Vector) |
| Queue/RT | `TaskQueue` iface: in-process asyncio (default) or ARQ+Redis; `PubSub` iface: in-memory or Redis; WS manager on top |
| Frontend | React 19 + TypeScript strict + Vite + Tailwind v4 + shadcn/ui, TanStack Query v5, react-router v7 (library mode), react-hook-form + zod, recharts, lucide |
| Widget | Preact iframe app + plain-TS loader (`window.Stept`), tour player runs in host DOM |
| Extension | Chrome MV3 recorder (TS + Preact popup) |
| Tests | pytest + pytest-asyncio (sqlite default, PG-marked tests), vitest + RTL, Playwright |
| API types | FastAPI OpenAPI export → `openapi-typescript` → typed frontend client (`make types`) |
| Auth | Argon2 passwords, JWT access (in-memory) + rotating refresh cookie, API keys (`sk_…`, sha256-stored), widget JWTs, HMAC identity verification |
| IDs | uuid7 (time-ordered) strings everywhere |
| Workspace scoping | Path-based: `/api/v1/w/{workspace_id}/…`; global: `/api/v1/auth|me|workspaces`; public: `/api/widget/*`, `/api/channels/*` (inbound), `/portal/*` |

## Monorepo layout

```
backend/    FastAPI app (app/core, app/models, app/schemas, app/services, app/api/v1, app/ai, app/rag, app/agents, app/channels, app/automation, app/dap, app/realtime, app/workers) + tests/
frontend/   React dashboard (src/features/*, src/components/ui = shadcn, src/api typed client)
widget/     loader.ts (host page) + Preact iframe app
extension/  MV3 tour recorder
e2e/        Playwright suites
docs/       PLAN, CONTRACTS, ARCHITECTURE, research/*, guides
```

## Waves & status

- [x] **R** Research agents → `docs/research/*.md` (chatwoot, onyx, vercel-ai, claude-agent-sdk)
- [x] **W0** Foundation — backend core, tenancy+auth+RBAC, stub tree, frontend shell, e2e harness. 29 tests. commit 78264e7
- [x] **W1** Backend domains: A directory, B conversations+realtime, C AI providers, D knowledge/RAG. 233 tests. commit 6511b23
- [x] **W2** E widget-API+channels, F automation+webhooks+reports+search, G agent engine (approval gates), H tours. 418 tests, deterministic. commit c810c2a
- [~] **W3** Frontend (3 agents IN FLIGHT): FE1 inbox+contacts, FE2 knowledge+ai/agents/approvals, FE3 automation+tours+reports+settings. schema.d.ts regenerated (120 paths/184 schemas) commit f69c3fd
- [~] **W4** Widget + extension (2 agents IN FLIGHT, parallel with W3): W1 widget/ (loader+iframe app+host-DOM tour player), W2 extension/ (MV3 recorder)
- [x] **W5** alembic 0001 migration + PG validation (commit 28a0f92); e2e journeys — widget→inbox→AI-citation, approval gate, dashboard nav (9 Playwright tests, commit 6644dcc); docs polish; `make verify` fully green. Merged to master.
- [x] **W6 (competitive parity)** — from source-level gap analysis of fresh Chatwoot+Onyx clones (`docs/COMPETITIVE.md`, `docs/research/{chatwoot,onyx}-gaps.md`). Backend (5 agents): channels **whatsapp/messenger/instagram/sms/line** (verify challenges, HMAC signatures, delivery receipts, WA 24h window), **SLA policies** (frt/nrt/rt, per-episode breach events, scheduler scan), **macros**, **campaigns** (ongoing widget proactive + one_off scheduled dispatch), knowledge connectors **sitemap/crawl/github/notion** + per-source `refresh_minutes` re-sync + deletion pruning + encrypted source secrets, **LLM rerank** pass, **search analytics** (query log playground/agent/copilot/widget) + 👍/👎 message feedback, `app.core.scheduler` (@scheduled registry + lifespan loop), alembic **0002** (validated up/down/up on PG). Frontend/widget (4 agents): channel config dialogs w/ webhook hints, SLA settings+thread card, macros tab+runner, campaigns page, knowledge connector dialog + **/knowledge/analytics** dashboard + rerank toggle, widget campaign engine (glob+time-on-page, seen-set) + feedback thumbs. Seeds: SLA policy on widget inbox, 2 macros, 1 ongoing campaign.
- [x] **Totals after W6:** 798 tests (backend 585 (+2 pg-only) + frontend 137 + widget 47 +
  extension 20 + e2e 9).
- [x] **Totals after W7:** **1383 tests** — backend 771 (+2 pg-only), frontend 324, widget 134,
  extension 92, dom-capture 45, e2e 17. `make verify` green (ruff + ruff-format + mypy on 213
  files + every build); e2e green on repeat runs; migration `bdc4e4b7de36` PG-validated
  up/down/up and `alembic check` reports no drift.
- [x] **W7 (DAP2 — full digital adoption platform + extension port + ingestion + editor)**
  Contracts: `docs/DAP2-CONTRACTS.md`. Research: `docs/research/{dap-competitors,old-extension-map}.md`.
  Session worktree: `.claude/worktrees/dap-suite` (branch `worktree-dap-suite`).
  - Scope: tours v2 (step types tooltip/modal/banner/hotspot/action/wait, driven "do-it-for-me"
    mode, frequency/schedule/priority/kind, self-healing selectors + breakage telemetry,
    analytics v2), **checklists**, **surveys** (NPS/rating/text/select), widget experiences
    bootstrap, **full Chrome extension port** from `/Users/ahoehne/repos/stept` (WXT + React
    side panel, real email/password login → 30d extension token, record/edit/preview/drive),
    shared `packages/dom-capture` selector+healing engine, RAG ingestion upgrades (multi-file
    batch upload, robots/URL-filters/concurrency/incremental crawl, editable authored docs),
    **TipTap editor** (markdown-persisting) for articles + knowledge docs.
  - Orchestrator prep (done): registries wired (routers/models/events/routes/sidebar "Adoption"
    group), `packages/dom-capture` workspace package, extension → WXT 0.20 + React 19, TipTap v3
    installed, `segments.contact_matches()` helper added.
  - Wave A (4 agents, done): A1 tours-v2 core + extension API + public media; A1b
    checklists+surveys; A2 ingestion; A3 dom-capture port. Commit 5c00bdc.
  - Wave B (5 agents, done): B1 tours admin UI + analytics, B1b checklist/survey builders,
    B2 TipTap + knowledge UI, B3 widget player v2, B4 extension v2. Commit fcf7877 (+ extension).
  - **Bugs found and fixed along the way** (each regression-tested): manual-trigger tours could
    never be started (the loader re-fetched an eligibility list that excludes them); the tour
    editor never sent `audience`, so segment targeting was API-only; a mid-tour reload re-fired
    `started` and inflated completion rates; the widget's history patch stacked on every re-boot;
    glob matching was case-sensitive against a case-insensitive backend; the widget markdown
    renderer dropped root-relative images, so editor-uploaded images vanished from step bodies;
    `SurveyResultsPage` mapped over `select`, which the API omits for surveys without a select
    question (would throw on open) — caught by the new type drift guard.
  - **Infrastructure**: `Storage.save_at()` so namespaced writes don't assume local disk;
    alembic `env.py` now ignores the startup-bootstrapped expression indexes (autogenerate kept
    proposing to drop the FTS + trigram indexes); `src/api/schema-drift.ts` fails the build when
    a hand-written API mirror diverges from the generated schema; `make test-frontend` now also
    runs the extension + dom-capture suites.
  - **Extension is now WXT + React with a side panel** (a popup dies on every page click, so it
    cannot anchor a recording session). Login is email/password → workspace pick → 30-day
    extension token; the access token is never persisted. Drive mode uses `chrome.debugger` for
    trusted input and degrades to synthetic events when attach is refused.
  - Deferred to roadmap (see dap-competitors.md parking lot): goals/A-B, localization, global
    rate limits, no-code event trackers, inline embeds, announcement feed, condition debugger,
    mobile SDKs, flow branching, remote agentic driving.
- [x] **W8 (in-app assistant)** — the embedded chat now answers *on the visitor's
  screen*: it finds and plays a published tour, composes a walkthrough anchored to
  the real UI, or (with the visitor's consent) clicks and types for them — while
  still answering knowledge questions with citations. Contract:
  `docs/IN-APP-ASSISTANT.md`. Ported engine-side from `/Users/ahoehne/repos/stept`
  (`extension/src/drive-controller.ts`, `services/ai_tools/browser_*`,
  `services/rag/*`, `routers/inline_ai.py`) with the driving surface turned inside
  out: no extension to install, the widget already lives in the page.
  - **Client-executed tools** (`app/agents/page_tools.py`): `page_snapshot/find/
    read/act/navigate/scroll/wait` + `show_guide`/`show_steps`, deferred to the
    browser through the engine's existing pause/resume machinery (new run status
    `awaiting_client`, new step kind `client_request`, `sweep_stale_client_waits`
    so a closed tab never strands a conversation).
  - **Host-page runtime** (`widget/src/page-agent.ts` + `packages/dom-capture/src/
    compact.ts`): overlay-first, viewport-first indexed element listing with live
    form state, char-budgeted and pageable; index → durable `Target` conversion so
    an AI-authored walkthrough survives re-renders.
  - **Guardrails**: off by default; acting needs the agent's `allow_actions` AND
    per-conversation visitor consent; the widget's own DOM and `data-stept-no-ai`
    subtrees are invisible; password fields never typed into; same-origin
    navigation; per-run cap on page changes; "no visible change after click"
    advisory.
  - **Retrieval** (`app/rag/{query,context}.py`): deterministic intent
    classification, query rewriting (filler/abbreviations/pronouns-from-history),
    multi-query expansion legs, a title leg, BM25 blend, and a token-budgeted
    context builder with query-focused compression — wired into the agent tool and
    the reply copilot.
  - **Editor AI** (`app/agents/writer.py` + `frontend/src/components/editor/
    AiMenu.tsx`): draft/outline grounded in the knowledge base with citations, plus
    improve/shorten/expand/simplify/fix/translate/title over a selection.
  - **Bugs found and fixed**: the widget-DOM exclusion list named surfaces that do
    not exist (`stept-checklist` vs the real `stept-cl-*`), so the assistant could
    see and offer to click the widget's own checklist button — now matched by class
    prefix; broadcasting a page op inside the parking transaction let a fast
    browser have its result rejected as stale (now `after_commit`); a resume that
    beat the result commit fabricated a "page did not respond" error (now
    `_ResumeNotReady` → queue retry); `ingest_document` abandoned a document
    silently when it never became visible (now logged); two pre-existing e2e DAP
    failures — the specs filled `#selector-N` while the tour editor keeps it behind
    an "Advanced" disclosure.
  - **Totals after W8:** **1731 tests** — backend 934 (+2 pg-only), frontend 383,
    widget 186, extension 100, dom-capture 107, e2e 21. `make verify` green,
    full e2e green.

- [x] **W9 (Chatwoot P0 inbox parity)** — the operational middle Chatwoot was ahead on, from
  the fresh gap analysis in `docs/CHATWOOT-BACKLOG.md` (chatwoot@0f3bb640, 2026-08-07).
  Worktree: `../stept-now-p0` (branch `feature/p0-inbox-parity`).
  - **Business hours** (§1.1): `app/core/business_hours.py` — DST-correct schedule maths;
    `working_hours` per inbox; `sla_policies.only_during_business_hours` so a Friday-evening
    ticket no longer breaches by Monday. Wall-clock stays the default.
  - **Participants + @mentions** (§1.2): implicit watchers (assignee, note author), `@name`
    resolution against members, notify + subscribe, leave-as-mute, `/mentions` feed.
  - **Filter DSL + saved views** (§1.3): one engine (`app/services/filters.py`) behind the
    conversation list, saved views, bulk targeting and report drill-down; server-driven field
    catalog so workspace attributes appear in the builder automatically.
  - **Bulk actions** (§1.4): `POST /conversations/bulk` looping the normal service functions;
    per-row failures reported, batch never rolled back.
  - **Reports** (§1.5): per-agent/team/inbox/tag/channel breakdowns, SLA attainment off the
    existing `sla_events`, CSV export, and drill-down where the row's own filter reproduces
    the number.
  - **Typed custom attributes** (§1.6): definitions + coercion, additive (undefined keys still
    pass through), feeding the filter catalog.
  - **Contact merge / block / CSV import+export** (§1.7): the migration-in door. Merge keeps a
    tombstone; block is enforced at widget boot and channel ingress.
  - **Bugs found and fixed along the way**: `is_open()` compared an un-normalised naive datetime
    against a localised window (so a naive `now` was read in the server's zone, not UTC); the
    422 handler serialised Pydantic's raw error list, so any `@model_validator` failure became a
    500 — now sanitised, and `input` is dropped so a rejected payload can't echo credentials
    back; jsdom lacks the Pointer Capture API, so no Radix `Select` was reachable in tests
    (polyfilled in `src/test/setup.ts` — this had silently blocked select-driven UI tests).
  - Migration `e133372bfc35`, PG-validated up/down/up with `alembic check` clean (needed
    `server_default` + an explicit drop so the DB matches the model).
  - **Measured after W9:** backend **1131** passed (+2 pg-only), frontend **424**, widget **187**
    — up from 934 / 383 / 186 at W8. The extension, dom-capture and e2e suites were unchanged by
    this wave and ran green as part of `make verify`, which passed end to end.

- [x] **W10 (MCP + remote browser drive)** — Stept is now an MCP server, so Claude Code /
  Claude Desktop / Cursor / ChatGPT can search the knowledge base, ask questions with
  citations, read tours and conversations, and **drive the user's real Chrome** through the
  extension. Contracts: `docs/MCP-CONTRACTS.md`. User guide: `docs/MCP.md`. Session worktree:
  `.claude/worktrees/mcp-parity` (branch `worktree-mcp-parity`). Ported from
  `/Users/ahoehne/repos/stept` (`api/app/mcp_server.py`, `services/agent_mcp/*`,
  `automation/src/gateway.ts`, `extension/src/{run-client,drive-controller,executor-extension}.ts`)
  into stept-now's own architecture — no Node automation service, the FastAPI app owns the
  extension WebSocket.
  - **Two MCP surfaces.** `/mcp` (official `mcp` SDK v2, streamable HTTP, stateless, JSON
    responses) with 28 tools; `/mcp/agents/{id}` (hand-rolled JSON-RPC — the tool list is
    computed per request from the agent's config, which the SDK can't express). Both
    authenticate with existing workspace API keys; a key with `agent_id` set is bound to one
    agent and refused everywhere else (including REST). `python -m app.mcp_stdio` for clients
    without HTTP.
  - **Mount mechanics** (`app/mcp/mount.py`): an ASGI shim copies `Authorization` into a
    contextvar because SDK tools run outside FastAPI's dependency graph; the scope is forwarded
    **untouched** (modern Starlette strips the mount prefix via `root_path` — rewriting it 404s
    everything); middleware rewrites bare `/mcp` → `/mcp/` because MCP clients don't follow the
    Mount's 307; the mounted app's lifespan is run explicitly (mounted lifespans are a no-op).
  - **Remote drive**: `WS /ws/extension` (extension token, per-workspace device registry,
    supersede-on-reconnect with 4000, ctrl_id-correlated futures, contract timeouts 60/30/60/900s)
    + `browser_*` tools. Extension side: WS run-client (3s reconnect, 20s ping, zombie-socket
    guard), a drive controller (popup LIFO follow, op serialization, "no visible change"
    advisory), a 16-op `stept-exec` content island on `@stept/dom-capture`, and CDP primitives
    (viewport-clipped screenshots ≤1568px, chords with macOS commands, per-char trusted typing,
    console/network ring buffers). Gated by a **"Let Stept control this browser"** switch;
    password fields are never read or typed into.
  - **Per-agent channel**: approval modes ask_in_chat / ask_in_stept / never_ask / deny, with
    `McpToolApproval` rows keyed by (key, agent, tool, canonical params hash) and an approvals
    UI. The old repo's Flask-tuple notification bug (`("", 204)` → HTTP 200 `["",204]`) is not
    ported: notifications return a real 202 with an empty body.
  - **One-click setup** (Settings → MCP · AI clients, and per agent): pick a client tab, press
    create, and the snippet below it is filled with the **real key** — the old repo only ever
    interpolated the key *prefix*, so its snippets never worked as pasted.
  - **RAG closures** so retrieval ≥ old stept: rerank switched on in every answer path (agent
    tool, copilot, both MCP ask paths; self-gates to >5 candidates, degrades to fused order),
    `<retrieved_context>` prompt-injection hardening, per-document `ai_searchable` opt-out
    (parity with the old `rag_indexed`), and PG prefix/trigram/short-query handling in global
    search plus the trigram index that backs it.
  - **Security review of the new surface** (keys, the contextvar carrying credentials, the
    browser-driving socket, approval bypass) — every finding below was PROVEN with a test
    before being fixed, and each fix ships with the test:
    - a custom action named `get_*` but issuing a POST escaped `deny` mode and the approval
      gate entirely — the action's author chose the name, and the caller is who we gate, so the
      HTTP method now decides and the name only refines a read-shaped method;
    - the agent endpoint never checked the key's scopes, so a `scopes=["read"]` key could fire
      every custom action the agent had (its own test fixture handed back an empty permission
      set, which is why nothing caught it);
    - approvals hashed and stored only the non-underscore arguments, so a caller could get
      `{subject}` approved and then run `{subject, _x}` — the executor substitutes `{_x}` into
      an action's URL or body like any other parameter. What is approved is now what runs;
    - the `STEPT_API_KEY` fallback applied to HTTP, not just stdio: an operator following our
      own stdio instructions in the API process would have turned `/mcp` into an
      unauthenticated, fully-scoped endpoint. It is now gated on the stdio entry point;
    - the docs promised password fields are never typed into, but only the DOM island enforced
      it — the drive path types through CDP and bypassed it. Both halves hold now;
    - the extension's `device_id` is chosen by the client and was the whole registry key, so any
      member could reuse a colleague's id: the victim's browser was closed as "superseded" and
      subsequent drive ops (and the results the AI reads) went to the attacker's browser. Slots
      are now namespaced by the authenticated user;
    - `/ws/extension` re-validated membership but not `tours:manage`, which is what minting the
      30-day token requires — a demoted member kept a working browser socket. Also: an ack now
      has to come from the device the op was addressed to.
    - Verified safe, with a kept regression test: the Authorization contextvar does NOT leak
      between concurrent requests (two in-flight calls parked on a barrier between capture and
      read each see only their own tenant; a deliberately-global variant fails the same test).
  - **The remote drive did not work in the shipped topology** (found at integration, before
    merge): the connected-browser registry was process-local while `deploy/docker-compose.prod.yml`
    runs `STEPT_WEB_CONCURRENCY=4` — the extension's socket lands on one worker and tool calls
    are balanced across all four, so ~3 of every 4 `browser_*` calls answered "no browser
    extension is connected" while one was. Discovery and dispatch now cross workers over
    `app.core.pubsub` (the same bus the realtime manager uses): a broadcast control topic for
    hellos/discovery/supersede, a unicast inbox per worker for dispatches and acks so
    screenshots never fan out, and a dispatch resolved to exactly one (node, device) before it
    is sent. A lone worker short-circuits the bus entirely — asserted by a test that watches the
    topic and proves no discovery frame is ever published. Supersede now works across workers
    too (previously a reconnect landing elsewhere left the dead socket registered and routable).
  - **Bugs found and fixed along the way**: the autogenerated migration added a NOT NULL column
    with no server default (would fail on any non-empty `documents` table) and referenced
    `app.core.db` without importing it; agent-bound MCP keys were accepted by the REST API,
    silently widening "let Claude talk to this one agent" into full workspace access; the agent
    card's tool-name preview slugified differently from the endpoint, so the UI could advertise
    names the server doesn't expose.
  - **Measured after W10** (on the merged tree, so these include W9): backend **1294** passed
    with PG up (0 skipped), frontend **443**, extension **199**, widget **187**, dom-capture
    **107**, e2e **21**. This wave itself added 154 backend, 19 frontend and 99 extension tests.
    `make verify` green; e2e green.
  - **Merged after W9 landed in parallel**, which needed real reconciliation rather than a
    text merge: both waves added a migration off `bdc4e4b7de36`, so `alembic upgrade head`
    would have failed on two heads — `a888d11bd47d` is re-parented onto `e133372bfc35` and the
    combined chain is PG-validated up/down/up (with a pre-existing document row, to prove the
    `ai_searchable` backfill) with `alembic check` clean. Only `docs/PLAN.md` conflicted in
    text; this entry was renumbered W9 → W10.
  - **Known follow-ups** (reviewed, deliberately not in this wave): neither MCP surface is rate
    limited — a leaked key can't read anything its scopes forbid, but `ask_agent` /
    `ask_knowledge_base` are unmetered LLM spend and `browser_run_tour` parks a request for up
    to 15 minutes; an approved MCP approval stays reusable for its 24h TTL with no revoke path;
    `browser_open`/`browser_navigate` don't restrict scheme or host, so a key with
    `tours:manage` can point the user's authenticated browser at an intranet address (Chrome
    blocks the exotic schemes, we don't).
  - **Verified live** (not just unit-tested): a running server answering real JSON-RPC on all
    three transports — HTTP `/mcp`, the per-agent endpoint, and the stdio bridge — plus a full
    remote-drive round trip with a fake extension on the real WebSocket (33 live assertions).

- [x] **W11 (integrations: OAuth framework + email done right + KB connectors + integrations UI)**
  Contracts: `docs/INTEGRATIONS-CONTRACTS.md`. Operator guide: `docs/INTEGRATIONS-SETUP.md`.
  Monetization strategy: `docs/MONETIZATION.md`. Worktree: `../stept-now-integrations` (branch
  `feature/integrations`). Built by 4 parallel agents against an orchestrator-written scaffold
  (models/schemas/permission/events/config/registries pre-wired); the agents hit the shared
  session limit in their final verify phase and the orchestrator finished the definition-of-done.
  - **Shared OAuth framework** (`app/integrations/{catalog,oauth,tokens,slack_install}.py`,
    `app/services/integrations.py`, `app/api/{v1/integrations,oauth_public}.py`): declarative
    provider catalog (the Chatwoot apps.yml pattern — adding a provider is a data entry, google/
    microsoft/slack/notion/confluence/zendesk shipped); signed-state JWT (15-min, purpose-pinned,
    open-redirect-guarded `return_to`); single global callback `/api/integrations/oauth/{provider}
    /callback` with the workspace carried in state; encrypted token storage + auto-refresh (300s
    skew, single-flight, rotating-refresh persistence for Atlassian/Notion); reauthorize lifecycle
    (`reauth_required` status + `integration.reauth_required` event); workspace-scoped OR
    env-var app credentials (workspace row wins). `Perm.INTEGRATIONS_MANAGE`. Slack connect
    provisions the channel inbox (idempotent per team). New tables `integration_connections` +
    `integration_app_credentials` (migration `b485d38185bf`).
  - **Email, properly** (`app/channels/{email,email_transports,email_sync}.py`,
    `app/api/channels/email.py`, `app/services/{email,inboxes}.py`): per-inbox transports —
    global / SMTP / Gmail-XOAUTH2 / Microsoft-XOAUTH2 / SES (stdlib SigV4, no boto) / Resend /
    Postmark / SendGrid / Mailgun; authenticated inbound (per-inbox `webhook_token`, per-ESP
    parsers with signature verification, SNS subscription-confirmation SSRF-guarded); the old
    bare `/inbound` open relay is closed (404 without a token); auto-generated forward-to
    addresses; IMAP polling (stdlib imaplib in a thread, LOGIN or XOAUTH2, UID cursor); proper
    Message-ID/References threading. **Security fix:** the pre-W11 inbound webhook was
    unauthenticated — any POST could inject mail into any workspace's inbox.
  - **Knowledge connectors** (`app/rag/{connectors,tasks}.py`, `app/{schemas,services}/
    knowledge.py`): Confluence Cloud (3LO OAuth via connection or Basic api-token; v2 spaces/pages,
    storage→text), Google Drive (folder recursion, Docs→markdown / Sheets→csv export, pdf/docx
    recorded-skip), Zendesk help center (incremental articles API, persisted `sync_cursor`,
    archived-as-removed pruning), plus a Notion OAuth mode. Tokens resolved through the seam.
  - **Frontend** (`features/settings/components/{IntegrationsPanel,EmailInboxWizard,
    integrations/*}`, `InboxConfigDialog`, `ChannelsPanel`, `features/knowledge/.../AddSourceDialog`,
    api/hooks): Settings → Integrations catalog page (category cards, one-click connect via
    `window.location.assign`, per-connection reauth/disconnect, "use your own app" credential
    forms with copyable redirect URI, `?connected=`/`?error=` toast handling); 3-step email
    transport wizard (transport cards → fields → finish with forward-to + inbound webhook URL);
    Confluence/Drive/Zendesk source tabs + Notion auth-mode toggle.
  - **Deploy fix found first** (shipped to master ahead of the wave, commits `92890ee`+`2806e7e`):
    the W10 MCP surface was dead in production — `deploy/Caddyfile` never routed `/mcp` to the API,
    so every MCP call hit the SPA nginx and 405'd; added a `/mcp` handle (960s read timeout for
    `browser_run_tour`) and a post-`up` `caddy reload` (compose doesn't restart caddy on a bind-mount
    config change). Verified live afterwards by driving the user's real Chrome via the extension.
  - **Bug found + fixed at integration:** autogenerated migration referenced `app.core.db` without
    importing it (same trap as W10's migration) — fixed. One pre-existing test
    (`test_blocked_contact_inbound_email_is_dropped`) asserted the bare inbound endpoint returns
    403 for a blocked contact; it now authenticates with the inbox webhook token (the bare
    tokenless path correctly 404s post-hardening) and still asserts the blocked-drop.
  - **Measured after W11** (SQLite): backend **1430** passed, 8 skipped (the 8 are `@pytest.mark.pg`);
    frontend **465** passed; `ruff` + `ruff format` + `mypy` (271 files) all clean; `tsc` clean.
    Migration `b485d38185bf` PG-validated up→down→up with `alembic check` reporting no drift.
  - **Verified live** (not just unit-tested): a running uvicorn against real Postgres drove the
    full Google OAuth round trip end to end — signup → workspace → `connect` (authorize URL with
    correct client_id/scope/redirect/state) → provider issues code → callback performs a real token
    exchange → connection persisted as `connected`; the stored access + refresh tokens are Fernet
    ciphertext at rest (verified by inspecting the `integration_connections` row directly).

- [x] **W12 (Actions SDK — client actions)** — the developer-facing half of the in-app
  assistant: a host page teaches the agent its own verbs in one line. Contracts:
  `docs/ACTIONS-SDK-CONTRACTS.md`. Docs: docs-site → Product → "Actions SDK".
  Worktree: `../stept-now-actions-sdk` (branch `feature/actions-sdk`). Single-session build.
  - **Surface**: `Stept('action', {name, description, params, confirm, approval,
    requiresIdentity, run})` + `Stept('removeAction', name)` (queue-safe pre-load); npm
    `@stept/js` (SSR-safe typed wrapper: `loadStept`/`stept`/`registerAction`) and
    `@stept/react` (`<SteptProvider>`, `useStept()`, `useSteptAction()` with
    mount/replace/unmount lifecycle). Handlers run in the page with the user's session —
    the server never calls the customer's API.
  - **Mechanics (no migration)**: defs ride widget message/conversation POSTs (stored in
    the same transaction that triggers the run — the FIRST turn already has them) and
    page-context POSTs (tri-state: null keeps, [] clears), into
    `conversation.attributes["client_actions"]` beside page-control consent.
    `resolve_agent_tools` merges them as `app_<name>` specs into `ToolPlan.client` +
    new `client_action_defs`; execution rides the existing `client_request` /
    `awaiting_client` defer-resume path with a new `{op:"action"}` wire op. The iframe
    renders a confirm card (Run / Not now) before anything crosses to the loader's
    `ActionRegistry`; declines resume the run as a declined tool result.
  - **Guardrails**: 20 defs/conversation + 16k stored budget + schema-size caps
    (normalize-don't-422, accepted names echoed for SDK console warnings); 10 action
    calls/run; def schema validated server-side before any browser round-trip;
    `approval: true` routes through the existing durable team gate; `requiresIdentity`
    defs are withheld (not refused) for anonymous visitors, decided at intake from the
    HMAC-verified principal; per-agent `settings.client_actions.enabled` off-switch
    (default ON — registering is the opt-in); workspace `CustomAction` names beat
    page-registered names; a stale replayed op falls back to confirm-ON; the
    stale-wait sweep gives `{op:"action"}` a person-sized 600s clock (vs 90s page ops)
    and resumes with "did not confirm". Dedupe guard: a socket+reload double delivery
    can no longer double-execute (covers page ops too).
  - **Dashboard/docs**: trace viewer labels `client_request` steps "Page op" vs
    "App action" (+ args); Agent builder gains the Client actions card; docs-site page
    with script-tag + React quickstarts, field reference, and three recipes; README
    "Actions SDK" bullet; `packages/{js,react}` publish-shaped (dev exports → src,
    `publishConfig` → dist; publishing itself deferred — needs the npm org; note:
    `@stept/widget` npm name is held by the internal iframe app and is load-bearing in
    CI/Makefile/e2e filters, so the public package is `@stept/js` pending a
    publish-time rename decision).
  - **Measured after W12** (SQLite): backend **1562** passed, 8 pg-marked skipped;
    frontend **477**; widget **205**; extension **199**; dom-capture **107**;
    `@stept/js` **5**; `@stept/react` **4**; e2e **23** (2 new action journeys: confirm
    → handler runs in the host page → run resumes; decline → declined result, page
    untouched). `make verify` green end to end; docs-site builds; no alembic change.
- [x] **W13 (i18n — 13 languages across the product)** — the repo had no i18n of
  any kind before this: no library, no catalogs, no locale column, no
  `Accept-Language`. Contract: `docs/I18N.md`. Session worktree:
  `.claude/worktrees/…` (branch `feature/i18n`).
  - **One catalog format across four runtimes** (backend, dashboard, widget,
    extension): flat dotted keys, `{{name}}` interpolation, CLDR plural
    siblings. No i18next — `Intl.PluralRules` gives the browsers correct
    categories for free, and `app/core/i18n.py` implements the same rules by
    hand for Python. `tParts` replaces `<Trans>`.
  - **The widget follows the language the visitor writes in**, over their
    browser header and over the host page's setting — the only signal that is
    evidence about the person rather than the machine. `lockLocale` opts out.
    Cost to the embed: +2.5 KB gzipped (catalogs are fetched assets; inlining
    all 13 cost +18 KB and was reverted).
  - **AI**: language detection (`app/services/language.py`, deterministic, not
    an LLM call), a reply-language rule in the agent prompt, and a soft locale
    boost in retrieval that never filters an English-only KB out of reach.
  - **Help centre**: per-locale articles + `translation_key`, slug unique per
    (workspace, locale), fallback per translation group rather than wholesale.
    Migration `a3f7c21e9b04`.
  - **Guard**: `make i18n-check` fails on missing keys, missing plural forms a
    locale's own rules can produce, stray placeholders, and registry drift.
  - **Coverage is measured, not assumed.** Backend and widget are complete in
    all 13. The dashboard's 1,036 extracted keys are 17% translated (all shared
    `common.*`, the auth flow, relative time); the rest falls back to English
    key by key, and the floor in `scripts/check-i18n.mjs` is what stops it
    regressing. Extension and landing are not localised yet.
  - **Totals after W13:** backend 1714 (+8 skipped), frontend 498, widget 251.
    `make verify` green.

- [x] **Dogfood fix wave (2026-08-11, dated — not numbered, landed while another wave
  was in flight)** — a live dogfood of the widget + Northplane tours on doktrace.com
  produced a 23-item backlog; this wave fixed the P0/P1 core in four parallel worktrees
  (`agent-truthful`, `tour-runtime`, `messenger-shell`, `bridge-extension`) merged via
  `integrate/dogfood-fixes`. Highlights:
  - **The false-failure loop is dead.** The engine's only tour-success signal was the
    messenger-iframe round trip (dropped whenever the thread was closed or the iframe
    reloaded); the 90s sweep then fabricated "timed out waiting for the page" and the
    model apologised while the tour played. Loader-fired tour telemetry
    (`started/step_viewed/step_blocked/completed/dismissed/step_error`) is now the
    authoritative signal: runs resume on `started`, finalize silently on
    `dismissed`/`completed`, and lifecycle lines are mirrored into the transcript
    (localized, 13 locales). `dismissed` is intent, never an error.
  - **Agent discipline:** 1 inbound → exactly 1 reply (`meta.reply_to`, cancel-and-merge),
    reply language follows the *latest visitor message* (stored locale only tiebreaks),
    `tour_autostart_policy` ask|auto|never (default ask → answer + `tour_offer` card;
    imperatives start immediately), auto-title, 24h auto-resolve → existing CSAT,
    `agent_run.updated {terminal}` clears the widget status line (90s client fallback).
  - **Tours travel:** per-step `url` recorded, persisted (normalize passthrough +
    content-signature) and honored by the player (navigate / "finding it…" wait ≤3s /
    explicit blocked card with Skip step · End tour, `step_blocked` telemetry — green
    tours can no longer be silently unplayable; blocked ⇒ at least yellow in
    `tours_health`). Resume pill instead of step-1 restarts; final-step CTA url works;
    modal steps always center; `advance_on_click` steps let the real click through the
    scrim and advance on it.
  - **Coexistence:** tour start collapses an open panel to a progress pill (restored
    after), launcher sits above the scrim, tooltip placement avoids widget surfaces,
    Esc dismisses the tour without leaking to the host.
  - **Messenger shell:** per-widget `brand_display_name`, agent name + AI chip on
    bubbles, unread opens the thread, word-boundary previews (tour events excluded),
    starter chips, "Talk to a person", federated Home search (articles + tours),
    localized times/labels (+27 keys × 13 locales), single close affordance.
  - **Bridge:** duplicate device registrations fixed (single-flight device id +
    epoch-guarded run client), drive-session owner affinity (heartbeats can't steal
    routing), `browser_open` reattaches instead of stacking tabs, snapshots are ~7 KB
    by default (screenshots opt-in as MCP image blocks, 1280px/q60), page-lifetime
    element ids + `browser_act(role=,name=)`, settle-before-extract + `browser_wait_for`,
    recorder stamps `url`/`advance_on_click`, content scripts no longer throw into
    customer consoles.
  - **Deliberately deferred** (still open from the backlog): token streaming, composer
    attachments, identify/HMAC wiring on customer sites, Home/Messages/Help full IA,
    campaigns surface in-widget, mobile-viewport pass, host-side loader strings i18n.
  - **Measured on the merged tree:** backend **1799** passed (+8 pg-skipped), frontend
    **498**, widget **326**, extension **233**; ruff/mypy/tsc clean; all builds green;
    alembic heads still **one** (no new migrations — JSON-attribute state only).

- [x] **i18n completion wave (2026-08-12, dated — finishes what W13 started)** — W13 shipped
  *thirteen* languages whose catalogs were mostly empty: backend and widget complete, the
  dashboard 17% translated, the extension and the marketing site not localised at all. Thirteen
  half-languages is worse than five whole ones — it puts a language picker in front of someone
  and then hands them an English screen. Contract: `docs/I18N.md` (rewritten). Session worktree
  `.claude/worktrees/i18n-five-complete`.
  - **Cut thirteen locales to five, then finished them.** Dropped pt-BR, nl, pl, tr, ja, ko,
    zh-CN and ar; kept **en, de, fr, es, it** and took every surface to 100%. The coverage floor
    in `scripts/check-i18n.mjs` went from `dashboard: 0.16` to **1 for every set** — a gap is no
    longer measured, it fails the build. Removing a locale is now a documented, legitimate move.
  - **Two surfaces localised from scratch.** The **extension** got its own runtime
    (`extension/src/i18n/`, same catalog format as the other three) wired through all 15 side
    panel/content-script/service-worker files — 205 keys, 201 static `t()` call sites plus
    dynamic `` t(`drive.status_${status}`) ``. The **landing** got build-time i18n: English at
    `/`, the rest under `/de/ /fr/ /es/ /it/`, one shared `HomePage.astro` per route, a footer
    `LangSwitcher`, `hreflang` + `x-default` alternates, and every locale route in the sitemap.
  - **The extension manifest, too.** Chrome reads the extension's name, description, toolbar
    tooltip and shortcut label *before any of our code runs*, via `_locales/<lang>/messages.json`
    and `__MSG_` placeholders — a separate mechanism from `src/i18n/`, and a harsher one: a key
    missing from the `default_locale` catalog makes Chrome refuse to load the extension. Now
    covered by the guard as a build failure rather than a coverage percentage.
  - **A silently-discarded setting, fixed.** `agent.settings.reply_language` was resolved through
    `normalize_locale`, which only knows locales we *render* — so cutting the locale set made a
    workspace that had configured Japanese replies fall back to mirroring the customer, with no
    error. Reply language and interface language are different questions: it now resolves through
    `reply_language_name` (~60 languages by name, `pt-BR` → Portuguese (Brazil), `de-AT` → German,
    unnameable → mirror the customer). Same doctrine as the detector, which deliberately
    recognises more languages than it renders so Dutch text scores as Dutch and returns None
    instead of scraping past the threshold as German.
  - **Guard made correct, not just stricter.** It read any key ending in a CLDR category as a
    plural, so `ai.create_one` — the slug for the button "Create one" — forced every translator to
    invent an `_other` twin (de and it had satisfied it by duplicating the string). A base is now
    plural only if English spells it with **two or more** categories; the three bogus twins are
    gone. It also polices the landing's locale list, so a locale can no longer exist in the
    product without a marketing page.
  - **Extraction defects the coverage number could not see.** Three *spliced fragments* — a
    translated fragment concatenated with hardcoded English (`{t('…connection_settings_for_this')}
    <span>{channel}</span> channel. Secrets are stored encrypted…`) — read as 100% covered while
    rendering half-English with English word order frozen in; now whole sentences via `tParts`.
    One codemod had rewritten the inside of a `//` comment, leaving a catalog key nothing renders.
    The **auth flow is now fully extracted** (subtitles, submit-button states, zod messages, OAuth
    failures, toast fallbacks — 44 new keys), and zod schemas moved off module scope, since a
    module-level `t()` freezes whichever language loaded first.
  - **Register consistency.** Spanish was split: the shipped widget/backend/auth strings used
    **tú** while every newly-written surface came back **usted**. Standardised on tú (~150 strings
    edited across five files). German is Sie, French vouvoiement, Italian tu — each verified
    against its own shipped catalog rather than assumed.
  - **One real plural, while the machinery was fresh.** `settings.ai_runs_this_month_no_runs`
    rendered "1 AI runs" — English had the defect too — because `t` keys plural selection on
    `vars.count` and the call site passed `runs`. Now `_one`/`_other` in all five locales with the
    call site passing `count`; a free-plan workspace really does pass through exactly one run.
  - **Deliberately deferred, measured not hand-waved:** ~60 ternary label pairs plus a handful of
    hardcoded props across 44 `.tsx` files remain unextracted — the codemod always skipped strings
    assembled inside a JSX expression. That is an *extraction* gap, not a translation gap: every
    key English defines exists in all five languages, and the 100% floor keeps it that way.
  - **Measured on the merged tree:** backend **1796** passed (+8 pg-skipped), frontend **502**,
    widget **327**, extension **246**, dom-capture **107**, `@stept/js` **5**, `@stept/react`
    **4**; ruff + ruff-format + mypy clean, tsc clean on all four TS packages, `pnpm -r build` and
    the landing build green, `make i18n-check` green (backend 21 keys, widget 124, dashboard
    1,076, extension 205, landing 157 — 4/4 locales complete on every set, plus 4 `__MSG_` keys
    across 5 manifest catalogs). Alembic heads still **one** — no migration; the shipped locale set
    is code, not data.

- [ ] Post-build notes for user: origin is `git@github.com:myfoxit/stept-now.git` (gh authed as
  `myfoxit`); master is pushed. Old stept containers on 8000/80/5173 are a PRIOR build —
  untouched. Postgres containers used for migration validation may still be up on 54329
  (`docker compose down` to stop). Use `docker compose` (v2) — the v1 `docker-compose` is broken
  by a pyenv SSL issue.
- [ ] **Waves can land in parallel now.** W9 and W10 were built simultaneously in separate
  worktrees and collided on the alembic head and on `docs/PLAN.md`. If you start a wave while
  another is in flight: branch from the current `origin/master`, and before merging re-check
  `alembic heads` (a second head means re-parent, not a merge revision) and the wave numbering.

### Agent-orchestration lessons (for future waves / resets)
- Agent completion notifications are reliable; a mid-task **silent stall** (~600s watchdog) sometimes doesn't self-report — when suspicious, check transcript freshness with `stat -L -f %m` on `<tasks>/<id>.output` (it's a SYMLINK; without -L you read the stale symlink mtime). Resume a stalled agent with SendMessage (its context is intact) or, if its work is already on disk, stop it and integrate yourself.
- Monitor tool's shell can't reliably `stat` the symlinked task files — don't build mtime monitors on them; rely on completion notifications + direct `stat -L` checks.
- Integration verify per wave: `ruff check app tests && mypy app && pytest -q` (+ run pytest ≥2× to catch flakes; fixed one queue-teardown isolation flake in W2).

## The build loop (per wave)

1. I write/refresh `docs/CONTRACTS.md` sections + per-agent spec (file ownership list, API surface, test expectations).
2. Launch wave agents in parallel (same checkout — ownership is disjoint by file; shared registries pre-stubbed in W0 so nobody edits shared files).
3. Each agent must run its own scoped verify (`pytest tests/<area>`, `ruff`, scoped `mypy` / `tsc`) before finishing.
4. I integrate: `make verify` (full), fix seams myself or SendMessage follow-ups to the owning agent, commit `feat(wave-N): …`.
5. Update this file + task list.

## Conventions quick-pointers

- All engineering conventions: `CLAUDE.md` (repo root). API/domain contracts: `docs/CONTRACTS.md`.
- Verify commands: `make verify` (everything), `make test-backend`, `make test-frontend`, `make e2e`.
- Never edit: `app/api/v1/__init__.py` router registry, `app/models/__init__.py` (pre-registered), frontend `src/router.tsx` (pre-registered), root `package.json` files (deps pre-installed) — these are orchestrator-owned.

## Decision log

- React over SvelteKit (user confirmed "react typescript"; shadcn is React-first).
- MIT license. Product name **Stept** (fits DAP "steps" + repo name).
- Dimensionless `vector` column by default (works with any embedding model); `make db-index-embeddings DIM=…` sets typed column + HNSW for scale. Local hash embedder (384-dim default) keeps RAG fully offline-capable.
- Mock chat provider is a first-class provider kind — deterministic, streams, does extractive RAG answers + scriptable tool calls → agent flows are e2e-testable without API keys.
- Alembic: single squashed `0001_initial` generated in W5 once schema settles; dev/test use `create_all`.
- No SLA policies in v1 (roadmap); CSAT, snooze, office-hours included.
- Old docker containers (ports 8000/80/5173) are a PREVIOUS stept build — do not touch, do not reuse their DB.
