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
- [x] **Totals:** 557 tests (backend 418 + frontend 83 + widget 27 + extension 20 + e2e 9), all green; mypy clean; all builds pass; schema PG-validated.
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
