# Stept — Engineering Conventions

Stept is an open-source Intercom+Fin alternative: FastAPI backend, React+shadcn frontend, embeddable widget, Chrome tour-recorder extension, Playwright e2e. Read `docs/PLAN.md` for status and `docs/CONTRACTS.md` for domain/API contracts before implementing anything.

## Golden rules (all agents)

1. **File ownership.** Only create/edit files explicitly assigned to you. NEVER edit shared registries: `backend/app/api/v1/__init__.py`, `backend/app/models/__init__.py`, `frontend/src/router.tsx`, any `package.json`/`pyproject.toml`/lockfiles. Your module files are pre-stubbed and already registered — fill them in.
2. **No new dependencies.** Everything you need is already declared. If something is genuinely missing, note it in your final report instead of adding it.
3. **Verify before finishing.** Run the scoped commands in your task (at minimum: backend → `cd backend && uv run ruff check app tests && uv run pytest tests/<your-area> -q`; frontend → `cd frontend && pnpm tsc --noEmit && pnpm vitest run <your-area>`). Fix what you broke, including pre-existing tests.
4. **Tests are part of the feature.** Every service/endpoint ships with tests: happy path + authz (403 for wrong role / cross-workspace) + one edge case. Frontend features ship vitest tests for non-trivial logic/components.
5. Match existing style. Read neighboring core files (`app/core/*`, `src/lib/*`) before writing.

## Backend

- Python 3.12, FastAPI, SQLAlchemy 2.0 **async** (`Mapped[]` / `mapped_column`), Pydantic v2. Line length 100, ruff + ruff-format, mypy must pass (`uv run mypy app`).
- Layout per domain `<d>`: models `app/models/<d>.py` → schemas `app/schemas/<d>.py` → service `app/services/<d>.py` → router `app/api/v1/<d>.py` (module-level `router = APIRouter()`). Tests in `backend/tests/<d>/`.
- **Portability:** DB must work on Postgres AND SQLite. Use `app.core.db` types: `GUID`, `PortableJSON`, `EmbeddingVector`, `utcnow()`, `uuid7()`. No raw dialect-specific SQL outside guarded branches (`session.bind.dialect.name == "postgresql"`).
- IDs are `uuid7()` strings. Timestamps tz-aware UTC. Money/none. Enums: `str` + `enum.StrEnum` stored as strings.
- Every workspace-scoped table has `workspace_id` FK, indexed. Every query filters by it. No exceptions.
- Errors: raise `app.core.errors` types (`NotFoundError`, `ForbiddenError`, `ConflictError`, `ValidationFailure`, …) — never raw `HTTPException` in services.
- AuthZ: routers use `deps.require_member(...)` / `deps.require_perm("<perm>")` dependencies; permission catalog in `app/core/permissions.py`.
- Pagination: `app.core.pagination` — `CursorPage` for feeds (conversations/messages), `OffsetPage` for admin lists.
- Events: emit domain events via `app.core.events.emit(...)` (names in `events.py`); realtime via `app.core.pubsub` topics `ws:{workspace_id}` / `conv:{conversation_id}`; background work via `app.core.queue.enqueue("task_name", ...)` with tasks registered `@task("task_name")`.
- Secrets at rest (provider keys, channel tokens): `app.core.security.encrypt_secret/decrypt_secret`. Never log secrets.
- Services take `(session, workspace_id, actor, …)` and return models/schemas; audit significant mutations via `app.services.audit.record(...)`.
- Tests: pytest-asyncio (auto mode). Use `conftest.py` fixtures: `client` (httpx ASGI), `session`, `workspace_ctx` (owner+workspace+headers factory). SQLite by default; mark PG-only tests `@pytest.mark.pg`. Mock external HTTP with `respx`. AI flows use the `mock` provider — never call real APIs in tests.

## Frontend

- React 19 + TS strict. Feature folders: `src/features/<area>/{api.ts,hooks.ts,components/,pages/}`. Pages are lazy-loaded from `src/router.tsx` (pre-registered — fill the stub page files).
- Data: TanStack Query v5 only (no useEffect fetching). Query keys `[<area>, workspaceId, ...]`. Mutations invalidate precisely. API calls via `src/api/client.ts` (`api.get/post/…` — typed, throws `ApiError`); realtime via `src/api/ws.ts` `useRealtime(topic, handler)`.
- UI: shadcn components from `@/components/ui/*`, icons `lucide-react`, toasts via `sonner`, forms react-hook-form+zod. Tailwind only — no inline styles, use design tokens (`bg-background`, `text-muted-foreground`, …). Dark mode must work (never hardcode colors).
- Dates: `src/lib/format.ts` helpers. State beyond server cache: small zustand stores in `src/stores/`.
- Accessibility: interactive elements are buttons/links with labels; keyboard works (dialogs, command palette).
- Tests: vitest + @testing-library/react; `src/test/setup.ts` provides QueryClient wrapper + api mock helpers (`mockApi`).

## Commands

- `make dev` (backend 8600 + frontend 5273), `make verify` (ruff+mypy+pytest, tsc+vitest+builds), `make test-backend`, `make test-frontend`, `make e2e`, `make types` (OpenAPI → TS), `make seed`.
- Backend alone: `cd backend && uv run uvicorn app.main:app --port 8600 --reload`. DB defaults to SQLite; `docker compose up -d` + `STEPT_DATABASE_URL=postgresql+asyncpg://stept:stept@localhost:54329/stept` for PG mode.

## Product vocabulary

Workspace (tenant) · Member (user in workspace, role: owner/admin/agent/viewer or custom role) · Team · Contact (end user) · Inbox (channel instance: widget/email/slack/telegram/api) · Conversation/Message (message.visibility: public|note) · Knowledge Source → Documents → Chunks · Article (help center) · AI Provider/Model · Agent (AI) + AgentRun + AgentStep + ApprovalRequest · Automation Rule · Tour (DAP) · Widget (embed).
