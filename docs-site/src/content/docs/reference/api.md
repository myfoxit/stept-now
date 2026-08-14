---
title: REST API
description: Base paths, authentication, pagination, errors, rate limits and a worked example.
---

Everything the dashboard does goes through the REST API — it is the same surface you
script against.

## Base paths

- API root: `/api/v1`
- Workspace-scoped resources: `/api/v1/w/{workspace_id}/…` — conversations, contacts,
  knowledge, agents, tours, reports, settings. Every request is checked against your
  membership (or API key) in that workspace.

## Authentication

**Users**: `POST /api/v1/auth/login` with email/password returns an access token in the
body (valid 15 minutes, send as `Authorization: Bearer …`) and sets a rotating, httponly
refresh cookie (30 days). `POST /api/v1/auth/refresh` mints a new access token; refresh
tokens are single-use with reuse detection.

**API keys**: create under **Settings → API keys** — keys look like `sk_stept_…`, are
workspace-bound, shown once, and sent as `Authorization: Bearer sk_stept_…`. Scopes:
`read` (all read endpoints), `write` (read + conversations, contacts, knowledge, tours
mutations), `admin` (everything except workspace deletion).

Two boundaries: API keys are rejected on **user-scoped** endpoints (`/me`, workspace
creation — those need a logged-in user), and keys minted on an agent's
[MCP channel card](/integrations/mcp/#per-agent-endpoint) are agent-bound and rejected on
the REST API entirely.

## Pagination

- **Feeds** (conversations, messages) use cursors:
  `{ "items": […], "next_cursor": "…" | null }` — pass `cursor=` to continue.
- **Lists** (documents, runs, admin tables) use offsets:
  `{ "items": […], "total": 123, "limit": 25, "offset": 0 }`.

## Errors

Every error is one envelope with the matching HTTP status:

```json
{ "error": { "code": "not_found", "message": "Source not found" } }
```

Codes include `bad_request` (400), `unauthorized` (401), `forbidden` (403), `not_found`
(404), `conflict` (409), `validation_failed` (422) and `rate_limited` (429). A `details`
object appears only when there is something in it — per-field errors on
`validation_failed`, for example.

## Rate limits

Abuse limits apply per route (for example login, signup, widget boot and public portal
endpoints); exceeding one returns 429 with the `rate_limited` code. Back off and retry.

## CORS

The authenticated API is same-origin: browsers may call it from the dashboard origin
(`STEPT_APP_BASE_URL`) plus anything you add to `STEPT_CORS_ORIGINS`. Only the public
surfaces — `/api/widget` (the embedded messenger) and `/portal` (the help center) — allow
any origin. Server-to-server calls with an API key are unaffected (CORS is a browser
concern).

## Outbound webhooks

To get pushed instead of polling, subscribe a URL to domain events — signed deliveries,
retries and a delivery log. See [Webhooks](/reference/webhooks/).

## OpenAPI

In development the interactive docs are at `/api/v1/docs` (spec:
`/api/v1/openapi.json`). In production they are off by default; set
`STEPT_EXPOSE_API_DOCS=true` to serve them.

## Worked example: crawl your docs site

```bash
API=https://app.stepped.ai
AUTH="Authorization: Bearer $TOKEN"   # login access token or sk_stept_… key
WS=<workspace_id>

# 1. Create a crawl source (does not sync yet)
SRC=$(curl -s -X POST $API/api/v1/w/$WS/knowledge/sources \
  -H "$AUTH" -H 'Content-Type: application/json' -d '{
    "type": "crawl",
    "name": "Docs site",
    "config": {
      "base_url": "https://docs.example.com/guides",
      "max_pages": 100,
      "include_patterns": ["/guides/*"],
      "refresh_minutes": 1440
    }
  }' | jq -r .id)

# 2. Trigger the sync (returns immediately with status "syncing")
curl -s -X POST $API/api/v1/w/$WS/knowledge/sources/$SRC/sync -H "$AUTH"

# 3. Poll until status is back to "idle" (or "error")
curl -s $API/api/v1/w/$WS/knowledge/sources/$SRC -H "$AUTH" | jq '.status, .document_count'

# 4. Search what was indexed
curl -s -X POST $API/api/v1/w/$WS/knowledge/search \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"query": "how do refunds work", "k": 5}' | jq '.results[].title'
```
