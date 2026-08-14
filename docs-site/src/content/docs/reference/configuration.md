---
title: Configuration
description: Every STEPT_* environment variable — defaults, what they do, and the production guardrails.
---

The backend reads settings from environment variables prefixed `STEPT_` (or a `.env` file
in `backend/`). Unknown variables are ignored.

## Core

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `STEPT_ENV` | `dev` | `dev` \| `test` \| `prod` — prod enables the boot guardrails |
| `STEPT_SECRET_KEY` | `dev-secret-key-change-me` | Signs **all** JWTs and derives the encryption key (see warning below) |
| `STEPT_BACKEND_PORT` | `8600` | API port |
| `STEPT_PUBLIC_BASE_URL` | `http://localhost:8600` | Public API origin — widget snippet `src`, OAuth redirect URIs |
| `STEPT_APP_BASE_URL` | `http://localhost:5273` | Dashboard origin — CORS allow-list seed, links in emails |
| `STEPT_CORS_ORIGINS` | `[]` | Extra allowed origins for the dashboard API |
| `STEPT_EXTENSION_WEB_STORE_URL` | empty | Chrome Web Store link shown for the recorder extension |

## Database, Redis, storage

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `STEPT_DATABASE_URL` | `sqlite+aiosqlite:///./stept.db` | Use `postgresql+asyncpg://…` in production (pgvector) |
| `STEPT_REDIS_URL` | unset | Enables the ARQ worker queue, Redis rate limits and pub/sub. Unset = in-process fallbacks (single node only) |
| `STEPT_DB_POOL_SIZE` | `10` | Connection pool size. **Read from the process environment only** — unlike every other row, an entry in `backend/.env` is ignored |
| `STEPT_STORAGE_DIR` | `./data/uploads` | Attachment/upload storage path |
| `STEPT_MAX_UPLOAD_MB` | `25` | Upload size cap |

## Auth & security

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `STEPT_ALLOW_SIGNUP` | `true` | Set `false` to make an internet-facing instance invite-only — signup is closed, members join via invitation |
| `STEPT_ACCESS_TOKEN_TTL_MINUTES` | `15` | Access-token lifetime |
| `STEPT_REFRESH_TOKEN_TTL_DAYS` | `30` | Refresh-cookie lifetime (rotating, reuse-detected) |
| `STEPT_REFRESH_ROTATION_GRACE_SECONDS` | `60` | Window in which a just-rotated refresh token is still accepted (parallel tabs racing the rotation) |
| `STEPT_INVITATION_TTL_DAYS` | `7` | Member invitation validity |
| `STEPT_RATE_LIMIT_ENABLED` | `true` | Toggle for the abuse rate limits |
| `STEPT_MCP_RATE_LIMIT_PER_MINUTE` | `120` | Per-key rate limit on the [MCP server](/integrations/mcp/); `0` disables |
| `STEPT_TRUSTED_PROXY_HOPS` | `0` | How many `X-Forwarded-For` hops to trust (e.g. `2` behind Cloudflare + Caddy) |
| `STEPT_EXPOSE_API_DOCS` | unset | OpenAPI/Swagger at `/api/v1/docs` — on unless `env=prod`; set to force either way |

## Email

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `STEPT_SMTP_HOST` | unset | **Unset = transactional email is logged to the API console instead of sent** — including the invite/reset links, so you can copy one out of the logs (invites, password resets) |
| `STEPT_SMTP_PORT` | `25` | |
| `STEPT_SMTP_USER` / `STEPT_SMTP_PASSWORD` | unset | |
| `STEPT_SMTP_TLS` | `false` | |
| `STEPT_EMAIL_FROM` | `Stept <no-reply@stept.local>` | From header |
| `STEPT_INBOUND_EMAIL_DOMAIN` | unset | Enables auto-generated `in-{hex}@{domain}` forwarding addresses on email inboxes |

Per-inbox email transports (SES, Resend, Postmark, SendGrid, Mailgun, per-inbox SMTP,
Gmail/Microsoft OAuth) are configured per inbox in the dashboard, not via env vars.

## Integration credentials (instance-level OAuth apps)

| Variable | Purpose |
| -------- | ------- |
| `STEPT_GOOGLE_CLIENT_ID` / `STEPT_GOOGLE_CLIENT_SECRET` | Gmail + Google Drive |
| `STEPT_MICROSOFT_CLIENT_ID` / `STEPT_MICROSOFT_CLIENT_SECRET` | Microsoft 365 |
| `STEPT_SLACK_CLIENT_ID` / `STEPT_SLACK_CLIENT_SECRET` / `STEPT_SLACK_SIGNING_SECRET` | Slack |
| `STEPT_NOTION_CLIENT_ID` / `STEPT_NOTION_CLIENT_SECRET` | Notion |
| `STEPT_CONFLUENCE_CLIENT_ID` / `STEPT_CONFLUENCE_CLIENT_SECRET` | Confluence |
| `STEPT_GITHUB_CLIENT_ID` / `STEPT_GITHUB_CLIENT_SECRET` | GitHub — the repo knowledge connector **and** "Sign in with GitHub" |
| `STEPT_GOOGLE_LOGIN_CLIENT_ID` / `STEPT_GOOGLE_LOGIN_CLIENT_SECRET` | A separate OAuth app for "Sign in with Google"; unset, login falls back to the integrations Google app above |
| `STEPT_OAUTH_BASE_OVERRIDE` | Dev/test only: redirect OAuth endpoints at a stub |

Workspaces can override any of these with their own OAuth app in the dashboard — the
workspace credential always wins. AI provider keys (OpenAI, Anthropic, …) are **not** env
vars: each workspace adds them in the dashboard, encrypted at rest.

## Billing (Stripe)

All unset (the default) = billing is off and **every feature is entitled** — the normal
state for a self-hosted instance.

| Variable | Purpose |
| -------- | ------- |
| `STEPT_STRIPE_SECRET_KEY` | Stripe API key |
| `STEPT_STRIPE_WEBHOOK_SECRET` | Verifies `POST /api/stripe/webhook` |
| `STEPT_STRIPE_PUBLISHABLE_KEY` | Used by the dashboard's checkout flow |
| `STEPT_STRIPE_PRICE_CLOUD` / `STEPT_STRIPE_PRICE_BUSINESS` | Price ids for the two paid plans |

## Knowledge & embedding

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `STEPT_EMBEDDING_DIM` | `384` | Vector dimension of the local fallback embedder |
| `STEPT_CRAWL_USER_AGENT` | `SteptBot/1.0 (+https://stepped.ai)` | User-Agent for website crawls — self-hosters should set their own so site owners can tell whose bot it is |
| `STEPT_CRAWL_PROXY_URL` | unset | Route crawler HTTP through a proxy |

## Scheduler & workers

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `STEPT_SCHEDULER_ENABLED` | `true` | Periodic jobs (SLA scans, scheduled syncs). In the compose stack it runs in a dedicated single-replica `scheduler` container and is disabled on `api` |
| `STEPT_SCHEDULER_TICK_SECONDS` | `15` | Scheduler tick interval |
| `STEPT_WEB_CONCURRENCY` | `4` (deploy) | uvicorn worker count (read by the container entrypoint) |
| `STEPT_API_KEY` | unset | MCP **stdio** mode only — the client credential; never authenticates HTTP |

## The secret key

`STEPT_SECRET_KEY` is load-bearing twice: it signs every JWT (sessions, widget tokens,
password resets, extension tokens, OAuth state) **and** the encryption key for stored
secrets — AI provider keys, channel credentials, OAuth tokens — is derived from it.
**Rotating it invalidates all stored credentials**, which then need re-entering. Generate a
strong one once: `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.

## Production guardrails

With `STEPT_ENV=prod` the app **refuses to boot** if the secret key is a known default or
shorter than 32 characters, or if either base URL is non-localhost `http://`. Database
schema comes from migrations in prod — the container entrypoint runs
`alembic upgrade head` on API start; auto table creation only runs in dev.
