---
title: Local development
description: Clone, seed, run — a full Stept dev environment with zero external services, plus the Postgres/Redis/Mailpit mode.
---

Stept develops with **zero external services**: SQLite, an in-process task queue, and a
deterministic mock AI provider are the defaults. You need Python 3.12+ with
[uv](https://docs.astral.sh/uv/) and Node 20+ with [pnpm](https://pnpm.io/).

## Clone, seed, run

```sh
git clone https://github.com/myfoxit/stept-now stept && cd stept
make setup    # backend (uv sync) + frontend (pnpm install)
make seed     # demo workspace + data (idempotent)
make dev      # backend on :8600, dashboard on :5273
```

Open **http://localhost:5273** and log in with the seeded account:

```
email:    owner@stept.dev
password: stept-demo
```

`make dev` also builds the embeddable widget loader when it is missing, so the widget and
tour player work on first run without a separate build step.

## Postgres mode

Production runs Postgres + pgvector and Redis; SQLite is close but not identical
(especially for search/RAG). To develop against the real thing:

```sh
docker compose up -d    # Postgres+pgvector :54329, Redis :63790, Mailpit
STEPT_DATABASE_URL=postgresql+asyncpg://stept:stept@localhost:54329/stept \
STEPT_REDIS_URL=redis://localhost:63790/0 \
  make dev
```

Outbound email lands in **Mailpit** at [http://localhost:18025](http://localhost:18025) —
invites and password resets included, no SMTP account needed. (Without any SMTP config,
emails are logged to the API console instead, links included.)

## Tests and checks

```sh
make verify         # everything except e2e: ruff + mypy + pytest, tsc + vitest + builds
make test-backend   # pytest (SQLite; pg-marked tests auto-skip without Postgres)
make test-frontend  # vitest: dashboard, widget, extension, dom-capture, SDKs
make e2e            # Playwright, spins up its own hermetic stack
```

## Regenerating the API client

The frontend's typed API client is generated from the backend's OpenAPI schema. After
changing backend routes or schemas:

```sh
make types
```

## Resetting the dev database

The dev SQLite database (`backend/stept.db`) is disposable and not migrated — after
pulling schema changes, `rm backend/stept.db && make seed`. Postgres databases migrate
with `cd backend && uv run alembic upgrade head`.
