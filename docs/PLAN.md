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
  - **Totals after W9:** **2004 tests** — backend 1131 (+2 pg-only), frontend 416, widget 186,
    extension 100, dom-capture 107, e2e 21. `make verify` green.

- [ ] Post-build notes for user: **no git origin configured** — merged to local master only, not pushed (user decides re GitHub; gh is authed as `myfoxit`). Old stept containers on 8000/80/5173 are a PRIOR build — untouched. A `build-postgres-1` container is up on 54329 (used for PG validation; `docker compose down` to stop). Use `docker compose` (v2) — the v1 `docker-compose` is broken by a pyenv SSL issue.

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
