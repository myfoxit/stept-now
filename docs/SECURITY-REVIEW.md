# Security review — 2026-08-04

Pre-deployment review of the whole surface: auth, RBAC, tenancy, the public widget
and portal APIs, inbound channel webhooks, outbound egress, file handling, the
markdown renderers, dependencies, and production configuration.

Every finding below was reproduced against a running instance with two tenants
before being fixed, and each fix ships with a regression test that fails without
it. The listed attacks were re-run against production after deployment and are
all blocked.

## Findings

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | **Stored XSS → account takeover.** `image/svg+xml` was uploadable and `GET /w/{ws}/files/{key}` served it `Content-Disposition: inline` with no CSP and no `nosniff`, same-origin with the dashboard. The refresh cookie is httpOnly but path-scoped to `/api/v1/auth`, so the payload could call `/auth/refresh` and mint access tokens. | SVG now carries `default-src 'none'` (the policy the public DAP media route already used); every file response sets `nosniff`. |
| 2 | High | **Cross-tenant file read.** `serve_file` checked only that the caller was a member of the workspace *in the path*, then read any storage key — private keys had no workspace segment. Confirmed: a member of workspace A fetched workspace V's file through A's own URL, byte-identical. | Private uploads live under `ws/{workspace_id}/…`; the route rejects anything outside the caller's namespace. Also closes read access to ingested knowledge documents through this route. |
| 3 | High | **Readable SSRF via custom agent actions.** `action.url` had no scheme or host validation, and `execute_custom_action` returns `response.text[:2000]` to the agent. Confirmed by creating an action against loopback and reading the response back through `/ai/actions/{id}/test`; `169.254.169.254` was accepted too. | Shared egress guard (`app/core/net.py`) at save time *and* request time. |
| 4 | High | **Meta webhook signature failed open.** `verify_meta_signature` returned `True` when no `app_secret` was stored, so anyone could POST a forged inbound WhatsApp/Messenger/Instagram message. Confirmed: an unsigned POST created a conversation from a spoofed sender. | Fails closed. Relays with no Meta app secret opt in per inbox with `config.allow_unsigned`. |
| 5 | High | **Prod could boot on the default `secret_key`.** `"dev-secret-key-change-me"` signs every JWT and derives the Fernet key for stored provider credentials; nothing stopped `env=prod` from using it. | `assert_production_ready()` refuses a default, short (<32 char), or non-https config at startup. |
| 6 | Medium | **Blind SSRF via outbound webhooks.** `_validate_url` checked the scheme only; IMDS, loopback, `localhost:22` and `[::1]` were all accepted, and the response code is stored. | Same shared egress guard, enforced at delivery time as well as save time. |
| 7 | Medium | **`identity_secret` readable by any member.** `WorkspaceOut.settings` was returned whole by `GET /w/{id}` and, embedded, `GET /me`. That key proves a widget visitor's `external_id`, so a **viewer** could forge an identity hash for any external id and read that end-user's conversations. | Redacted from the schema; readable from a `WORKSPACE_MANAGE`-gated `GET /w/{id}/identity-secret`. |
| 8 | Medium | **Slack inbound crossed tenants.** `_find_slack_inbox` cannot filter by workspace (Slack addresses us by team) and fell back to the oldest Slack inbox install-wide. Confirmed: an event with an unmatched `team_id` landed in an unrelated workspace. | Exact `team_id` match required, with a single-unclaimed-inbox fallback for self-hosters. |
| 9 | Medium | **No security headers.** HSTS, CSP, `nosniff`, frame-options, referrer and permissions policy were all absent. | Added on every response. Framing is still permitted for `/widget-assets` and `/portal`, which customer sites embed by design. |
| 10 | Medium | **Rate limiting covered 3 endpoints.** Only signup/login/password-reset. 50 anonymous `/api/widget/boot` calls were all served, each minting a Contact + ContactInbox; widget message send (which drives paid AI runs) was unlimited. | Limits on boot, conversation create, message create, plus backstops on the channel and portal router trees. The limiter is now Redis-backed when configured, so a limit means one thing across workers instead of being multiplied by worker count. |
| 11 | Medium | **Rate limits were ineffective behind Cloudflare** (found post-deploy). Cloudflare appends the visitor's address and Caddy appends Cloudflare's, so with `trusted_proxy_hops=1` the app bucketed on a Cloudflare edge IP and a flood spread across hundreds of buckets. | `STEPT_TRUSTED_PROXY_HOPS=2` for this topology. Verified live: 45 requests → 15 limited. |
| 12 | Low | Swagger UI and `openapi.json` were public in every environment. | Off when `env=prod` unless `STEPT_EXPOSE_API_DOCS=true`. |
| 13 | Low | Password policy is length ≥ 8 with no complexity or breach check. | Not changed — flagged as a product decision. |
| 14 | Low | `react-router` 7.x carries a CSRF advisory (GHSA-qwww-vcr4-c8h2) scoped to RSC mode, which this SPA does not use. The extension's build toolchain (`shell-quote`, `tmp`, `adm-zip`) has critical/high advisories. | Neither ships to the server; the extension toolchain is excluded from the web image. Worth upgrading react-router on its own schedule. |
| 15 | Info | `assert_public_url` resolves DNS and httpx resolves again, so a hostname can change answers in between (DNS rebinding). Closing it needs connection-time pinning. | Documented in `app/core/net.py`. The guard raises the bar to "attacker controls authoritative DNS with a sub-timeout TTL". |

## Bugs found while testing the deployment

Not security issues, but both were production-breaking and hidden by the same gap.

1. **Workspace creation was broken on Postgres — nobody could sign up.**
   `create_workspace` added the `Workspace` and the owner `Membership` in one
   flush. `Membership` has only a raw `workspace_id` FK and no `relationship()`
   to `Workspace`, and SQLAlchemy's unit of work orders inserts from mapper
   relationships rather than column ForeignKeys, so it was free to insert
   `memberships` first. Postgres rejects that with a FK violation.

2. **SQLite was not enforcing foreign keys at all**, which is why 987 tests never
   caught #1: the dev/test database silently accepted rows Postgres refuses.
   `build_engine` now sets `PRAGMA foreign_keys=ON`; the full suite still passes.

3. **A test-isolation race behind two flaky tests.** `InProcessQueue.enqueue`
   started an asyncio task immediately, so an ingestion task ran concurrently with
   the next request. Harmless against Postgres (a pooled connection per session),
   but the test database is in-memory SQLite behind a `StaticPool` where every
   session shares one DBAPI connection — two interleaved transactions clobber each
   other, so a document created with a 201 could vanish. Under `env=test`,
   enqueueing now records the task and `drain()` runs them serially.

## What held up

Tenancy scoping is consistently applied — every `session.get(Model, id)` is
followed by an explicit `workspace_id` check in the paths sampled. Argon2 for
passwords; JWTs pin `HS256` and check the token type; refresh tokens rotate with
reuse detection; every HMAC comparison uses `compare_digest`; Slack enforces a
5-minute timestamp skew; LINE and Twilio fail closed on a missing secret. The
markdown renderers escape before transforming and no attribute-injection or
`javascript:` bypass was found. Storage has path-traversal guards, and the public
media namespace was already CSP-neutering SVG. All SQL is parameterized, including
the pgvector and full-text paths. The RBAC matrix is coherent and protects the
last owner. Backend dependencies are current with no known CVEs.

## Reproducing

The probe scripts used are not checked in (they create real accounts). The
regression tests cover the same ground:

```sh
cd backend
uv run pytest tests/core/test_egress_guard.py tests/core/test_hardening.py \
              tests/core/test_api_keys_and_files.py tests/core/test_workspaces_and_rbac.py \
              tests/channels/test_whatsapp.py tests/channels/test_messenger.py \
              tests/channels/test_slack.py -q
```
