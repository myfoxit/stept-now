---
title: Self-hosting
description: Run the full Stept stack on your own server with Docker Compose.
---

Stept is a FastAPI backend + React frontend, shipped as prebuilt images. The production
stack is Docker Compose behind Caddy (automatic HTTPS).

## The stack

| Service     | Role |
| ----------- | ---- |
| `api`       | FastAPI app (uvicorn, N workers) |
| `worker`    | ARQ task queue worker — AI runs, ingestion/crawling, webhook delivery |
| `scheduler` | exactly one loop for periodic jobs (SLA breaches, scheduled syncs) — never scale past 1 |
| `web`       | nginx serving the dashboard SPA + the embeddable widget bundle |
| `postgres`  | Postgres 16 with pgvector |
| `redis`     | task queue + rate limiting + realtime pub/sub |
| `caddy`     | the single public entry point, terminates TLS via Let's Encrypt |

The reference `docker-compose.prod.yml`, `Caddyfile` and an annotated `env.example` live in
the repo's [`deploy/`](https://github.com/myfoxit/stept-now/tree/master/deploy) directory.

## Minimal setup

```sh
mkdir -p /opt/stept && cd /opt/stept
# copy docker-compose.prod.yml as docker-compose.yml, Caddyfile, and env.example as .env
# then edit .env:
#   DOMAIN, APP_DOMAIN, SERVER_IP, ACME_EMAIL
#   STEPT_SECRET_KEY   ← python -c 'import secrets; print(secrets.token_urlsafe(48))'
#   POSTGRES_PASSWORD
#   STEPT_PUBLIC_BASE_URL / STEPT_APP_BASE_URL = https://<your app domain>
docker compose pull
docker compose run --rm api migrate   # apply database migrations first
docker compose up -d
```

Check health:

```sh
curl https://<your-app-domain>/api/v1/healthz
# {"status":"ok","version":"...","database":"ok"}
```

## Production guardrails

With `STEPT_ENV=prod` the app **refuses to boot** if the secret key is a known default or
shorter than 32 characters, or if a base URL is plain `http://`. Fail-loud beats silently
signing tokens with a public constant.

Two things to know about `STEPT_SECRET_KEY`:

1. It signs every JWT (sessions, widget tokens, password resets).
2. The encryption key for stored secrets (AI provider keys, channel tokens) is derived from
   it — **rotating it invalidates all stored provider credentials**.

## Development mode

For local hacking you don't need any of this: the backend defaults to SQLite and an
in-process task queue.

```sh
cd backend && uv run uvicorn app.main:app --port 8600 --reload
cd frontend && pnpm dev
```

## AI providers

Model API keys are **not** instance configuration — each workspace adds its own provider
keys in the dashboard, encrypted at rest. The instance never needs an LLM key in `.env`.
