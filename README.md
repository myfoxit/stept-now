<div align="center">

# Stept

**The open-source, AI-first customer support platform.**
An Intercom + Fin alternative you can self-host: shared inbox, multi-channel,
world-class RAG, a configurable AI agent engine with human approval gates, and
built-in product tours.

</div>

---

> **Status: feature-complete v0.1.** The backend (multi-tenancy, RBAC, conversations,
> channels, multi-provider AI, RAG, the agent engine with approval gates, automation,
> reports, tours), the React dashboard, the embeddable widget, and the Chrome tour
> recorder are all built and tested — **550+ automated tests** across pytest, vitest, and
> Playwright, all green with zero external services required. See `docs/PLAN.md` for the
> build history.

## Why Stept

Support tooling shouldn't be a black box you rent. Stept gives you the whole stack,
self-hostable and MIT-licensed:

- 🗂️ **Shared team inbox** — conversations across every channel in one place, with
  assignment, teams, private notes, tags, canned responses, priorities, snooze, and CSAT.
- 💬 **Multi-channel** — embeddable web chat widget, email, Slack, Telegram, **WhatsApp
  (Cloud API)**, **Facebook Messenger**, **Instagram DM**, **SMS (Twilio)**, **LINE**, and
  a public API channel — with signature-verified webhooks, delivery receipts, and
  WhatsApp's 24h-session-window rules handled for you. All conversation logic is
  channel-agnostic; adding a channel is one adapter.
- 📚 **World-class RAG** — upload docs, crawl sites (recursive or sitemap), or connect
  **GitHub** and **Notion** → parse → chunk → embed → **hybrid retrieval** (dense vectors +
  full-text, fused with Reciprocal Rank Fusion, neighbor-expanded, optional **LLM rerank**)
  with inline citations. Sources re-sync on a schedule, prune deleted pages, and your
  help-center articles are first-class RAG sources. **Search analytics** show what people
  ask, what got zero results, and how often the AI deflects a conversation.
- 🤖 **AI agents ("Fin", but yours)** — configurable AI support agents with tools,
  guardrails, and **human approval gates**: an agent can search your knowledge base, tag
  or resolve a conversation, collect details, call your own HTTP actions, or hand off to a
  human — and any tool can be set to *require approval*, pausing the run (durably, across
  restarts) until a teammate approves from the inbox. Every run has a full, inspectable trace.
- 🔌 **Bring your own AI** — OpenAI, Anthropic, Google, Ollama, or any OpenAI-compatible
  endpoint, configured per workspace with encrypted keys. A deterministic **mock provider**
  ships in the box so every AI feature runs offline with zero API keys.
- 🧭 **Product tours (DAP)** — build step-by-step in-app guides with a Chrome recorder
  extension; the widget plays them in your app. Onboarding without shipping code.
- 📣 **Campaigns & SLAs** — proactive in-app messages (URL + time-on-page triggered) and
  one-off scheduled sends to a segment; SLA policies with first-response / next-response /
  resolution targets, breach events, and inbox badges. Plus **macros** (one-click
  multi-action shortcuts) and 👍/👎 feedback on AI answers.
- 🔐 **Enterprise-ready** — multi-workspace tenancy, RBAC with builtin + custom roles,
  team management, invitations, API keys with scopes, audit log, outbound webhooks.
- 🧪 **Built to iterate** — backend (pytest), frontend (vitest), and end-to-end
  (Playwright) tests all run with **zero external services**, so `make verify` is fast and
  hermetic.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | FastAPI · SQLAlchemy 2 (async) · Pydantic v2 · Python 3.12 · uv |
| Database | PostgreSQL 16 + pgvector (prod) · SQLite (tests & zero-dep dev) — one portable schema |
| Realtime / jobs | WebSockets · pub/sub + task queue (in-memory, or Redis + ARQ) |
| Frontend | React 19 · TypeScript · Vite · Tailwind v4 · shadcn/ui · TanStack Query |
| Widget | Preact iframe app + a tiny loader script |
| Extension | Chrome MV3 tour recorder |
| Tests | pytest · vitest · Playwright |

## Quick start (zero dependencies)

You need **Python 3.12+** (with [uv](https://docs.astral.sh/uv/)) and **Node 20+** (with
[pnpm](https://pnpm.io/)). No database or Redis required to start — Stept defaults to
SQLite, in-memory queues, and the mock AI provider.

```bash
git clone <your-fork-url> stept && cd stept
make setup          # installs backend (uv) + frontend (pnpm) deps
make seed           # creates a demo workspace + data (idempotent)
make dev            # backend on :8600, dashboard on :5273
```

Open **http://localhost:5273** and log in with the seeded account:

```
email:    owner@stept.dev
password: stept-demo
```

> **Upgrading an existing checkout?** The dev SQLite database (`backend/stept.db`) is
> disposable and is *not* migrated automatically — after pulling schema changes, run
> `rm backend/stept.db && make seed`. (A half-migrated DB makes `make seed` fail and roll
> back, which then breaks login.) Postgres deployments migrate with
> `cd backend && uv run alembic upgrade head`.

## Full-fidelity mode (Postgres + Redis + Mailpit)

Develop against what production runs — especially when touching search/RAG:

```bash
make services       # Postgres+pgvector, Redis, Mailpit via docker compose
STEPT_DATABASE_URL=postgresql+asyncpg://stept:stept@localhost:54329/stept \
STEPT_REDIS_URL=redis://localhost:63790/0 \
  make dev
```

Configuration is all `STEPT_`-prefixed env vars (or a `.env` file) — see
[`.env.example`](.env.example) for the full list. Providers, models, and channel
credentials are configured per-workspace in the UI and encrypted at rest.

## Commands

```bash
make dev            # run backend + dashboard
make verify         # ruff + mypy + pytest, tsc + vitest + builds (everything but e2e)
make test-backend   # pytest (SQLite; pg-marked tests auto-skip without Postgres)
make test-frontend  # vitest (dashboard + widget)
make e2e            # Playwright end-to-end (spins up its own hermetic stack)
make types          # export OpenAPI → regenerate the typed frontend API client
make seed           # (re)seed the demo workspace
```

## Embedding the chat widget

Once your workspace has a widget inbox, drop two lines into any site (the exact snippet,
with your key, is shown in **Settings → Channels**):

```html
<script>window.SteptSettings = { workspaceKey: "wk_your_key" }</script>
<script src="https://your-stept-host/widget-assets/loader.js" async></script>
```

For logged-in users, verify identity with an HMAC of their id (computed server-side with
your workspace's identity secret) so conversation history is secure across devices.

## Architecture

```
backend/    FastAPI app — core (config/db/security/rbac/events/pubsub/queue/storage/ws),
            models · schemas · services · api/v1 · ai (providers) · rag · agents ·
            channels · automation · dap · realtime · workers
frontend/   React dashboard (feature-sliced, typed API client generated from OpenAPI)
widget/     embeddable chat: loader.ts (host page) + Preact iframe app
extension/  Chrome MV3 tour recorder
e2e/        Playwright suites (hermetic full-stack)
docs/       PLAN (build status) · CONTRACTS (domain/API contracts) · research · guides
```

Design notes and the reasoning behind key decisions live in `docs/` — including distilled
studies of Chatwoot, Onyx, the Vercel AI SDK, and the Claude Agent SDK that informed the
data model, retrieval pipeline, streaming protocol, and approval-gate state machine.

## Contributing

Stept is built to be hacked on. Before a PR: `make verify` should be green. Conventions
live in [`CLAUDE.md`](CLAUDE.md); domain contracts in [`docs/CONTRACTS.md`](docs/CONTRACTS.md).

## License

[MIT](LICENSE) © Stept contributors.
