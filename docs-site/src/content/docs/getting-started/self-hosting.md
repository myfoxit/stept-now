---
title: Self-hosting
description: Run the full Stept stack on your own server with Docker Compose, built from source.
---

Stept is a FastAPI backend + React frontend. The production stack is Docker Compose behind
Caddy (automatic HTTPS). **Prebuilt images are not published yet** — the supported path
today is building from source on the server, which the standalone compose file does for
you (`--build` below).

## The stack

| Service     | Role |
| ----------- | ---- |
| `api`       | FastAPI app (uvicorn, N workers) — runs migrations on start |
| `worker`    | ARQ task queue worker — AI runs, ingestion/crawling, webhook delivery |
| `scheduler` | exactly one loop for periodic jobs (SLA breaches, scheduled syncs) — never scale past 1 |
| `web`       | nginx serving the dashboard SPA + the embeddable widget bundle |
| `postgres`  | Postgres 16 with pgvector |
| `redis`     | task queue + rate limiting + realtime pub/sub |
| `caddy`     | the single public entry point, terminates TLS via Let's Encrypt |

The standalone compose file, its Caddyfile and an annotated env template live in the
repo: [`deploy/docker-compose.standalone.yml`](https://github.com/myfoxit/stept-now/blob/master/deploy/docker-compose.standalone.yml),
[`deploy/Caddyfile.standalone`](https://github.com/myfoxit/stept-now/blob/master/deploy/Caddyfile.standalone),
[`deploy/env.example`](https://github.com/myfoxit/stept-now/blob/master/deploy/env.example).

## Setup

```sh
git clone https://github.com/myfoxit/stept-now stept && cd stept
cp deploy/env.example deploy/.env   # compose reads deploy/.env automatically
# edit deploy/.env — the required vars:
#   DOMAIN               ← public hostname; everything is served at https://$DOMAIN
#   ACME_EMAIL           ← the email for Let's Encrypt
#   POSTGRES_PASSWORD
#   STEPT_SECRET_KEY     ← python -c 'import secrets; print(secrets.token_urlsafe(48))'
docker compose -f deploy/docker-compose.standalone.yml up -d --build
```

The stack serves everything at one domain: `STEPT_PUBLIC_BASE_URL` and
`STEPT_APP_BASE_URL` are derived as `https://$DOMAIN` — you don't set them.

The first `up --build` compiles the backend image and the frontend/widget bundles —
expect a few minutes. Migrations run automatically when the API container starts
(the entrypoint runs `alembic upgrade head`); there is no separate migrate step.

Check health:

```sh
curl https://<your-domain>/api/v1/healthz
# {"status":"ok","version":"...","database":"ok"}
```

Then open the dashboard, sign up (the first account), and create your workspace. On an
internet-facing instance, consider
[`STEPT_ALLOW_SIGNUP=false`](/reference/configuration/) once your own account exists —
signup closes and teammates join via invitation.

## SMTP is effectively required

Without `STEPT_SMTP_HOST`, transactional email (invites, password resets) is **logged to
the API container's console instead of sent** — the log line includes the actual link, so
you can fish an invite URL out of `docker compose logs api` in a pinch, but you cannot
invite teammates from the UI in any reasonable way. Set the `STEPT_SMTP_*` vars early.

## Backups

Two things hold state: the Postgres volume and the uploads volume. Nightly:

```sh
# database
docker compose -f deploy/docker-compose.standalone.yml exec -T postgres \
  pg_dump -U stept stept | gzip > backup-$(date +%F).sql.gz

# uploads (attachments) — the volume is mounted at /data/uploads in the api container
docker run --rm --volumes-from $(docker compose -f deploy/docker-compose.standalone.yml ps -q api) \
  -v $(pwd):/backup alpine tar czf /backup/uploads-$(date +%F).tar.gz /data/uploads
```

Ship both files off the machine. Restore is `gunzip | psql` plus untarring the uploads.

## Upgrading

```sh
git pull
docker compose -f deploy/docker-compose.standalone.yml build
docker compose -f deploy/docker-compose.standalone.yml up -d
```

The API container migrates the schema on start.

:::caution
A Postgres database that was **first created by dev mode** (auto table creation, not
migrations) has no `alembic_version` table, so the first `alembic upgrade head` would try
to re-create everything. Stamp it once before the first migrated start:
`cd backend && uv run alembic stamp head`.
:::

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
in-process task queue. See [Local development](/getting-started/local-development/).

## AI providers

Model API keys are **not** instance configuration — each workspace adds its own provider
keys in the dashboard, encrypted at rest. The instance never needs an LLM key in `.env`.
