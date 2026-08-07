# W11 — Integrations: OAuth framework, email done right, KB connectors, integrations UI

> Wave contract. Read `CLAUDE.md` first. Research inputs: Chatwoot source analysis + Intercom
> docs + provider API docs (validated 2026-08). Parity bar: Chatwoot's channel/integration UX;
> Intercom's one-click OAuth connect + Fin's connector catalog.
>
> **Orchestrator owns** (already written — do NOT edit): `app/models/integration.py`,
> `app/schemas/integrations.py`, `app/core/{permissions,config,events}.py`, all `__init__`
> registries, `app/main.py`, `frontend/src/features/settings/sections.ts`,
> `frontend/src/features/settings/pages/SettingsLayout.tsx`, the alembic migration (written at
> integration), `docs/PLAN.md`. Everything else per the ownership table below.

## Why (one paragraph)

Stept has 10 channel types but email is webhook-ingress-only with one global SMTP sender and an
unauthenticated inbound endpoint; there is zero OAuth anywhere (every channel is token-paste +
manual webhook copying); knowledge connectors are a closed set (sitemap/crawl/github/notion-token).
Chatwoot ships per-inbox IMAP/SMTP + one-click Gmail/Microsoft OAuth + an integrations registry;
Intercom ships one-click everything + Zendesk/Confluence/Notion KB sync. This wave closes those
three holes with a shared OAuth framework so every future integration is a catalog entry, not a
bespoke build.

## Shared architecture (all agents)

### Provider catalog (`app/integrations/catalog.py`, BE-A)

Declarative registry, Chatwoot's `apps.yml` pattern in Python:

```python
@dataclass(frozen=True)
class ProviderSpec:
    id: str                      # "google" | "microsoft" | "slack" | "notion" | "confluence" | ...
    name: str                    # "Google (Gmail & Drive)"
    category: str                # "email" | "knowledge" | "channel" | "app"
    auth: str                    # "oauth2" | "token" | "none"
    description: str
    doc_slug: str                # anchor into docs/INTEGRATIONS-SETUP.md
    authorize_url: str = ""      # base authorize endpoint (oauth2 only)
    token_url: str = ""
    scopes: tuple[str, ...] = ()
    extra_authorize_params: Mapping[str, str] = field(default_factory=dict)
    credential_fields: tuple[str, ...] = ("client_id", "client_secret")  # operator-supplied
    uses_pkce: bool = False
```

Registered providers this wave (exact values in "Provider cheat sheet" below): `google`,
`microsoft`, `slack`, `notion`, `confluence`, `zendesk` (auth="token" — no OAuth, catalog entry
for UI completeness). The catalog is data; adding a provider later must not require touching the
OAuth core.

### Models (orchestrator-written, in `app/models/integration.py`)

- `IntegrationConnection` — one authorized account: `workspace_id`, `provider`, `status`
  (`connected|reauth_required|error|revoked`), `account_label` (e.g. the Gmail address, the
  Slack team name), `scopes` JSON list, `access_token_encrypted`, `refresh_token_encrypted`
  (both Fernet via `app.core.security`), `token_expires_at`, `meta` JSON (provider-specific:
  Atlassian `cloud_id`+`site_url`, Slack `team_id`+`bot_user_id`, Notion `bot_id`+
  `workspace_name`, MS `tenant`), `created_by`. Multiple connections per provider are allowed
  (two Gmail inboxes = two connections).
- `IntegrationAppCredential` — workspace-scoped OAuth app credentials: `workspace_id`,
  `provider`, `client_id`, `client_secret_encrypted`, `extra` JSON (e.g. Slack
  `signing_secret`), unique `(workspace_id, provider)`. Resolution order everywhere:
  workspace row → env (`settings.google_client_id` / `..._client_secret` etc.). If neither
  exists the provider shows as "needs setup" in the catalog API.

### OAuth core (`app/integrations/oauth.py`, BE-A)

The Chatwoot/Intercom anatomy, exactly:

1. `POST /api/v1/w/{ws}/integrations/{provider}/connect` (perm `integrations:manage`) →
   `{"authorize_url": ...}`. Server-built URL: catalog spec + resolved credential +
   `redirect_uri = settings.public_base_url + "/api/integrations/oauth/{provider}/callback"` +
   `state`.
2. `state` = signed compact JWT (reuse the JWT helpers in `app.core.security`), 15-min expiry,
   claims `{ws, provider, purpose:"integrations.connect", nonce, return_to}`. `return_to` is a
   path WITHIN the app (validated: must start with `/`), default
   `/settings/integrations`.
3. Public callback `GET /api/integrations/oauth/{provider}/callback?code&state` (in
   `app/api/oauth_public.py`, no auth — it's a browser redirect): verify state → resolve
   credential → exchange code at `token_url` (httpx; Notion uses HTTP Basic
   `base64(client_id:client_secret)`; everyone else posts form fields) → provider "whoami" call
   to fill `account_label` + `meta` (see cheat sheet) → create `IntegrationConnection` →
   audit + emit `integration.connected` → 302 to
   `settings.app_base_url + return_to + "?connected={provider}"`. On any failure: 302 to
   `return_to + "?error={safe_code}"` — never a raw 500 to a human.
4. `app/integrations/tokens.py` — **the seam BE-B/BE-C consume** (monkeypatch it in your tests):

```python
async def get_connection(session, workspace_id: str, connection_id: str) -> IntegrationConnection
    # NotFoundError if missing/wrong workspace; refuses status == "revoked".
async def get_valid_access_token(session, connection: IntegrationConnection) -> str
    # Returns a live access token. Refreshes when token_expires_at is within 300s
    # (single-flight per connection id), persists rotated refresh tokens (Atlassian/Notion
    # rotate!), commits via the caller's session. On refresh failure: set status
    # "reauth_required", emit integration.reauth_required, raise IntegrationAuthError.
class IntegrationAuthError(SteptError)  # in app/integrations/oauth.py; maps to 409 at the API
```

5. All provider HTTP base URLs must come from module-level constants that tests/dev can
   override via settings (`settings.oauth_base_override: dict[str,str]`, empty by default) so
   the full callback round-trip is provable against a local stub server — this is how the
   live verification will be run.

### Endpoints (BE-A, `app/api/v1/integrations.py`; shapes in `app/schemas/integrations.py`)

| Method/path (under `/w/{ws}`) | Perm | Behavior |
|---|---|---|
| `GET /integrations` | `integrations:manage` | Catalog merged with state: per provider `{id,name,category,auth,description,configured,connections:[ConnectionOut],credential:{client_id,has_secret,redirect_uri,fields} \| null, doc_slug}` |
| `POST /integrations/{provider}/connect` | same | → `{authorize_url}` (409 `integration_not_configured` when no credential) |
| `DELETE /integrations/connections/{id}` | same | Revoke at provider when supported (Google revoke endpoint; else best-effort), mark `revoked`, audit, emit `integration.disconnected` |
| `POST /integrations/connections/{id}/reconnect` | same | Same as connect but `state.connection_id` set → callback UPDATES that connection in place (reauthorize) |
| `PUT /integrations/{provider}/credentials` | same | Upsert workspace app credential (secret write-only) |
| `DELETE /integrations/{provider}/credentials` | same | Remove workspace credential (falls back to env) |

Public router `app/api/oauth_public.py`: the callback above + `GET /api/integrations/oauth/{provider}/start?state=...`
(re-entry helper that rebuilds the authorize redirect from a still-valid state — used by "retry").
Mounted in `main.py` (already wired) with a rate limit.

### Events (orchestrator-added to `events.py`)

`INTEGRATION_CONNECTED = "integration.connected"`, `INTEGRATION_DISCONNECTED = "integration.disconnected"`,
`INTEGRATION_REAUTH_REQUIRED = "integration.reauth_required"`.

### Permission

`Perm.INTEGRATIONS_MANAGE = "integrations:manage"` (already added; owner/admin have it via
ALL_PERMS; NOT in agent/viewer; NOT in api-key read/write scopes, IS in admin scope).

---

## Agent BE-A — framework + Slack install + tests

**Owns:** `app/integrations/*` (fill the stubs), `app/services/integrations.py`,
`app/api/v1/integrations.py`, `app/api/oauth_public.py`, `backend/tests/integrations/*`.

Beyond the shared architecture above:

- **Slack one-click install** (`app/integrations/slack_install.py`): provider `slack` connect
  flow; scopes `channels:history,channels:read,chat:write,groups:history,groups:read,im:history,
  im:read,im:write,users:read,team:read`. Callback exchange at
  `https://slack.com/api/oauth.v2.access` returns `{access_token (xoxb-…), team:{id,name},
  bot_user_id}`. After storing the connection, **provision the channel inbox**: find an enabled
  `slack` inbox with matching `team_id` or create one (name = team name) via
  `app.services.inboxes`, setting `config.team_id` and secrets `{bot_token, signing_secret}` —
  `signing_secret` comes from the app credential's `extra["signing_secret"]` (credential_fields
  for slack: `client_id, client_secret, signing_secret`). The existing
  `/api/channels/slack/events` inbound + sender then work unchanged. Reconnect updates the
  stored `bot_token`.
- Google connections are shared by email (BE-B) and Drive (BE-C): scopes requested =
  `https://mail.google.com/ https://www.googleapis.com/auth/drive.readonly openid email` plus
  `access_type=offline&prompt=consent` (refresh token). `account_label` = email from the
  `id_token` (decode WITHOUT verification is fine — it came over TLS from the token endpoint —
  but document that). Microsoft: scopes `offline_access openid email
  https://outlook.office365.com/IMAP.AccessAsUser.All https://outlook.office365.com/SMTP.Send`;
  tenant `common`; `account_label` = `preferred_username`/`upn` claim (UPN matters for SMTP
  login — Chatwoot ships this bug-fix; copy it).
- Confluence (Atlassian 3LO): after token exchange call
  `GET https://api.atlassian.com/oauth/token/accessible-resources`; if exactly one site, store
  `meta.cloud_id`+`meta.site_url`; if several, store the list under `meta.sites` and leave
  `cloud_id` unset — BE-C's source config picks one. Scopes: `read:confluence-content.all
  read:confluence-space.summary search:confluence offline_access`. Refresh tokens ROTATE —
  `get_valid_access_token` must persist the new one every refresh.
- Notion public-OAuth: `owner=user`, token exchange via Basic auth, store `bot_id`,
  `workspace_name`. No scopes param.
- **Tests** (`tests/integrations/`): authorize-URL construction (params, scopes, state
  round-trip, redirect_uri), state expiry + tamper rejection, callback happy path per provider
  against respx (token exchange + whoami + connection row + redirect location), reconnect
  updates in place, refresh-with-rotation persists, refresh failure → `reauth_required` +
  event, credential resolution order (workspace row beats env), authz (403 for agent role,
  cross-workspace 404), Slack callback provisions the inbox (and is idempotent per team_id).
  Everything respx-mocked; NO live HTTP.

## Agent BE-B — email, properly

**Owns:** `app/channels/email.py`, NEW `app/channels/email_transports.py`, NEW
`app/channels/email_sync.py`, `app/api/channels/email.py`, `app/services/email.py`,
`app/services/inboxes.py`, `backend/tests/channels/test_email*.py` (+ new files).

**Inbox config contract** (email inboxes; `Inbox.config` / secrets via
`services.inboxes.set_secrets`):

```
config: {
  address,                      # the public from/support address
  transport: "global" | "smtp" | "gmail" | "microsoft" | "ses" | "resend"
             | "postmark" | "sendgrid" | "mailgun",       # default "global" = today's behavior
  connection_id,                # gmail/microsoft: IntegrationConnection id
  forward_to,                   # auto-generated "in-{12 hex}@{settings.inbound_email_domain}"
  webhook_token,                # auto-generated urlsafe token — REQUIRED on inbound URLs
  smtp: {host, port, username, security: "starttls"|"tls"|"none"},
  imap: {enabled, host, port, username, poll_minutes (min 2, default 3)},
  ses: {region},  mailgun: {domain, base: "us"|"eu"},
}
secrets: { smtp_password, imap_password, ses_access_key_id, ses_secret_access_key,
           resend_api_key, postmark_server_token, sendgrid_api_key, mailgun_api_key,
           mailgun_signing_key }
```

`services/inboxes.py`: on email-inbox create, auto-fill `forward_to` (when
`settings.inbound_email_domain` set) + `webhook_token`; validate transport-specific required
fields on create/update (`ValidationFailure` with a field map). Gmail/Microsoft transport also
auto-sets `imap.enabled=true` with the provider hosts (`imap.gmail.com` / `outlook.office365.com`,
port 993) and SMTP hosts (`smtp.gmail.com` / `smtp.office365.com`, 587 starttls) — user sees them
read-only, exactly like Chatwoot.

**Outbound** (`email_transports.py`): `async def send_via_inbox(session, inbox, *, to, subject,
html, text, reply_to, headers) -> str | None` returning the provider message id when known.
Transports:

- `global`: today's `services.email.send_email` path (unchanged fallback).
- `smtp`: per-inbox aiosmtplib with the inbox's host/port/security/credentials.
- `gmail`/`microsoft`: SMTP XOAUTH2 — token via `app.integrations.tokens.get_valid_access_token`,
  auth string `base64("user=" + login + "\x01auth=Bearer " + token + "\x01\x01")`, sent through
  aiosmtplib's low-level command interface after STARTTLS. Login = `smtp.username` (defaults to
  connection `account_label`). On `IntegrationAuthError`: raise so delivery marks failed with
  "reauthorize Gmail/Microsoft in Settings → Integrations".
- `ses`: SESv2 `POST https://email.{region}.amazonaws.com/v2/email/outbound-emails` with a
  minimal SigV4 signer implemented locally with stdlib hmac/hashlib (`_sigv4_headers(...)`) —
  no boto. Body: `{FromEmailAddress, Destination, Content:{Simple|Raw}}`; use Raw (build MIME
  with stdlib `email.message`) so threading headers survive.
- `resend`: `POST https://api.resend.com/emails` bearer, `{from, to, subject, html, text,
  reply_to, headers}`.
- `postmark`: `POST https://api.postmarkapp.com/email` header `X-Postmark-Server-Token`.
- `sendgrid`: `POST https://api.sendgrid.com/v3/mail/send` bearer.
- `mailgun`: `POST https://api{".eu" if eu}.mailgun.net/v3/{domain}/messages` basic auth
  `api:{key}`, form-encoded.

`app/channels/email.py` sender: replace the direct `send_email` call with `send_via_inbox`;
**always set a generated `Message-ID`** (`<{uuid7}@{domain of address}>`) on outbound, store it
in `message.meta["message_id"]`, and build `References` as chain (last inbound reference + our
last outbound id) — keep the existing reply+{conversation_id} Reply-To and quoted-tail logic.

**Inbound** (`app/api/channels/email.py`):

- Existing generic JSON endpoint moves to `POST /api/channels/email/inbound/{inbox_id}/{token}`;
  token must match `config.webhook_token` (404 on mismatch — don't confirm existence). The old
  bare `/inbound` stays for one release but requires a token query param when any email inbox
  has one set; when it cannot resolve an inbox it 404s (closes today's open relay).
- Per-ESP endpoints (same path family `/inbound/{provider}/{inbox_id}/{token}`) parsing each
  provider's payload into the SAME internal `InboundEmail` shape:
  - `resend` (JSON; svix-style signature verify with `mailgun_signing_key`-like secret? NO —
    Resend inbound uses svix headers; verify HMAC-SHA256 base64 over `{id}.{timestamp}.{body}`
    with the endpoint secret stored in inbox secrets `resend_webhook_secret`; document field),
  - `postmark` (JSON: FromFull/ToFull/TextBody/HtmlBody/MessageID/Headers → In-Reply-To),
  - `sendgrid` (multipart form inbound parse: fields `from,to,subject,text,html,headers`),
  - `mailgun` (form: `sender,recipient,subject,body-plain,stripped-text,Message-Id`; verify
    `signature` = HMAC-SHA256(timestamp+token, signing_key) when signing key present),
  - `ses` (SNS envelope: handle `SubscriptionConfirmation` by GETting `SubscribeURL` —
    SSRF-guard with `app.rag.connectors.check_public_url` — and `Notification` containing SES
    receipt JSON with `content` (raw MIME base64) or S3 action pointer; parse raw MIME with
    stdlib `email` → InboundEmail; token in path is the auth).
  All enqueue the same routing as today (reply+id → in_reply_to → address match). HTML-only
  bodies: strip to text (reuse `_to_text`).
- **IMAP poll** (`email_sync.py`): task `email_imap_poll` + `@scheduled("email_imap_scan",
  every_seconds=60)` scanning enabled email inboxes with `imap.enabled` due by `poll_minutes`.
  Poll with stdlib `imaplib` inside `asyncio.to_thread` (no new deps): UID SEARCH from stored
  `imap_uid_cursor` (in inbox config, updated after each poll), fetch RFC822, parse with stdlib
  `email` (walk multipart, prefer text/plain, fallback html→text), route through the same
  inbound path, mark seen optional (config `imap.mark_seen` default false — read-only).
  Auth: password mode (LOGIN) or XOAUTH2 (gmail/microsoft transports) via
  `imap.authenticate("XOAUTH2", ...)` with the tokens seam. Auth failures → inbox-level flag
  `config.reauth_required=true` + emit `integration.reauth_required` (UI banner) — Chatwoot's
  Reauthorizable, simplified.
- **Tests**: transport dispatch per provider against respx (SES SigV4 signature header shape
  asserted, not just called), XOAUTH2 auth-string construction, Message-ID/References threading
  chain over a 3-message conversation, every inbound parser (fixture payloads per provider →
  message row + contact + threading), webhook token enforcement (401/404 paths), mailgun/resend
  signature verify (valid + invalid), SNS confirmation handshake (respx + SSRF guard), IMAP
  poll against a monkeypatched imaplib fake (cursor advance, XOAUTH2 vs LOGIN selection,
  auth-failure → reauth flag), inbox create validation per transport, authz on inbox endpoints.
  Mark nothing pg-only.

## Agent BE-C — knowledge connectors

**Owns:** `app/rag/connectors.py`, `app/rag/tasks.py`, `app/schemas/knowledge.py`,
`app/services/knowledge.py`, `backend/tests/knowledge/test_connectors_*.py` (new files; don't
touch other knowledge tests).

New source types (extend `CONNECTOR_SOURCE_TYPES` and the schema literal): `confluence`,
`gdrive`, `zendesk`; upgrade `notion` with OAuth mode. All follow the existing
`fetch_github`-style contract: `async def fetch_x(config, secrets, *, session=None,
workspace_id=None) -> list[FetchedDoc]` — note the two new kwargs: connectors that use OAuth
connections need a session to resolve tokens via `app.integrations.tokens`; plumb them through
`_sync_connector` (it has both at hand). Config schemas validated in `services/knowledge.py`
mirroring existing types (max_pages caps, refresh_minutes min 5).

- `confluence`: config `{auth: "oauth"|"token", connection_id?, base_url?, email?, space_keys:
  [..] (empty = all global spaces), max_pages (cap 500), refresh_minutes}`, secrets
  `{api_token}` (token mode). OAuth mode: base = `https://api.atlassian.com/ex/confluence/
  {cloud_id}/wiki`, bearer from tokens seam; `cloud_id` from `config.cloud_id` or connection
  `meta.cloud_id`. Token mode: base = `{base_url}/wiki` with Basic `email:api_token` (Cloud).
  Enumerate: `GET /api/v2/spaces?type=global&limit=100` (filter to `space_keys` when set) →
  `GET /api/v2/spaces/{id}/pages?body-format=storage&limit=100` (cursor `Link`/`_links.next`).
  Convert storage XHTML → text with the existing crawl HTML-to-text util. `FetchedDoc.uri` =
  the page's `_links.webui` absolute URL; title = page title. Incremental: keep it
  hash-based (existing `content_hash` skip) — no CQL watermark this wave (note in docstring).
- `gdrive`: config `{connection_id, folder_ids: [..] (empty = "shared with the integration"
  is NOT a thing on Drive — require ≥1 folder id), max_files (cap 500), refresh_minutes}`.
  `files.list` with `q='{folder}' in parents and trashed=false`, recurse subfolders
  (`mimeType='application/vnd.google-apps.folder'`), fields `id,name,mimeType,modifiedTime,
  webViewLink,size`. Google Docs → `files/{id}/export?mimeType=text/markdown`; Sheets →
  `text/csv`; Slides → `text/plain`; `.md/.txt/.html` → `alt=media` (≤2MB); PDF/DOCX → SKIP
  with a per-doc note this wave (parser reuse is a follow-up; do not silently drop — record in
  sync error summary). uri = webViewLink.
- `zendesk`: config `{subdomain, locale (default "en-us"), refresh_minutes}`, secrets
  `{email, api_token}` (Basic `email/token:api_token`). Incremental:
  `GET https://{subdomain}.zendesk.com/api/v2/help_center/incremental/articles?start_time={cursor}`
  (cursor persisted in `config.sync_cursor` by the fetch — plumb a config-writeback, the
  sitemap connector already mutates source config for lastmod state… if it does not, persist
  via a returned-state mechanism: `fetch_zendesk` returns docs plus sets
  `config["sync_cursor"]`; `_sync_connector` already persists source on success). Body =
  sanitized HTML → text via existing util; drafts (`draft: true`) skipped; uri = `html_url`;
  deletion pruning stays hash/uri-based (incremental export includes archived flags — treat
  archived as missing).
- `notion`: add `auth: "oauth"` + `connection_id` mode using the tokens seam (token mode
  unchanged). No other behavior change.
- Respect 429/`Retry-After` with capped sleeps (Zendesk incremental is 10 req/min: page size
  1000 makes one call per sync typical; still honor).
- All remote fetches SSRF-guarded where URLs are user-supplied (`base_url`, subdomain builds).
- **Tests** per connector against respx: enumeration + pagination + content conversion +
  uri/title mapping, OAuth vs token auth paths (tokens seam monkeypatched), zendesk cursor
  advance + archived pruning, gdrive folder recursion + export mime routing + skip-note for
  pdf, confluence space filter + storage→text, notion oauth mode, config validation errors,
  sync end-to-end through `sync_source` upserting documents (existing harness pattern).

## Agent FE-D — integrations UI + email wizard + source dialogs

**Owns:** `frontend/src/features/settings/components/IntegrationsPanel.tsx` (+ new components
under `features/settings/components/integrations/`), `features/settings/api.ts` additions,
`features/settings/components/InboxConfigDialog.tsx`, `features/settings/components/
ChannelsPanel.tsx`, `features/knowledge/components/AddSourceDialog.tsx`, related hooks + tests.
Do NOT touch `sections.ts` / `SettingsLayout.tsx` (already wired to your panel).

- **Integrations page** (`/settings/integrations`): card grid grouped by category (Email,
  Knowledge, Channels, Apps). Card: icon (lucide or inline brand-neutral glyph), name,
  description, state chip — `Connected (n)` / `Needs setup` / `Not connected`. Actions:
  `Connect` (POST connect → `window.location.assign(authorize_url)`), per-connection row
  (account_label + status badge incl. `reauth_required` warning + `Reconnect` + `Disconnect`
  confirm), `Use your own app` collapsible → credential form (client_id, secret write-only
  password field, provider-specific extra fields, and a copyable read-only **redirect URI**).
  Handle `?connected=` / `?error=` query params on mount → toast + clear param + invalidate.
  Zendesk card: no OAuth — short explainer pointing at the knowledge source dialog.
- **Email inbox wizard**: replace the flat email spec in `InboxConfigDialog` with a stepped
  flow (also used from ChannelsPanel "New channel" when type=email): step 1 pick transport —
  cards: Gmail (one-click), Microsoft 365 (one-click), SMTP/IMAP, Amazon SES, Resend,
  Postmark, SendGrid, Mailgun, "Forwarding only" (global transport). Gmail/Microsoft cards:
  if no google/microsoft connection exists → button "Connect Google/Microsoft first" that
  deep-links to the integrations panel; else a connection select. Step 2: address + transport
  fields (hosts read-only for gmail/microsoft, port/security selects for smtp, region for ses,
  domain+region for mailgun, secrets as password fields with "unchanged" placeholder). Step 3
  (finish): show the **forward-to address** (copyable, with "set up forwarding at your
  provider" hint), the **inbound webhook URL** for the chosen ESP (copyable, with per-provider
  one-line instructions), and IMAP polling toggle + interval where relevant. Keep every other
  channel's existing spec-driven dialog behavior intact.
- **Knowledge source dialog**: add tabs Confluence / Google Drive / Zendesk (fields per BE-C
  config contract; connection selects listing connections of the right provider with a
  "Connect …" deep-link when empty; secrets as password fields), extend Notion tab with an
  auth-mode toggle (token | Notion account via OAuth → connection select). Show
  `sync_cursor`-style fields never; show refresh interval consistently (`RefreshField`).
- **API layer**: `features/settings/api.ts` — `listIntegrations`, `connectIntegration`,
  `disconnectConnection`, `reconnectConnection`, `putCredentials`, `deleteCredentials` typed
  per the endpoint table (types from `src/api/schema.d.ts` if regenerated; else local types
  matching the schemas file exactly — orchestrator regenerates `make types` at integration).
  Query keys `['integrations', workspaceId]`; precise invalidation.
- **Tests** (vitest): IntegrationsPanel render states (needs-setup vs connected vs reauth),
  connect click hits endpoint + redirects (assert `window.location.assign` mock), credential
  form write-only secret + redirect-uri copy, query-param toast handling, email wizard
  transport switching renders correct fields + validation + finish screen shows forward-to +
  webhook URL, AddSourceDialog new tabs submit correct config/secrets payloads, notion oauth
  toggle. Use `mockApi` helpers.

---

## Provider cheat sheet (validated against provider docs, 2026-08)

| Provider | Authorize | Token | Scopes (exact) | Whoami / label | Notes |
|---|---|---|---|---|---|
| google | `https://accounts.google.com/o/oauth2/v2/auth` | `https://oauth2.googleapis.com/token` | `https://mail.google.com/ https://www.googleapis.com/auth/drive.readonly openid email` | `id_token.email` | `access_type=offline&prompt=consent`; refresh non-rotating; revoke `https://oauth2.googleapis.com/revoke?token=` |
| microsoft | `https://login.microsoftonline.com/common/oauth2/v2.0/authorize` | `https://login.microsoftonline.com/common/oauth2/v2.0/token` | `offline_access openid email https://outlook.office365.com/IMAP.AccessAsUser.All https://outlook.office365.com/SMTP.Send` | `id_token.preferred_username` (UPN) | `prompt=select_account`; refresh returned each exchange |
| slack | `https://slack.com/oauth/v2/authorize` | `https://slack.com/api/oauth.v2.access` | bot scopes CSV (see BE-A) via `scope=` | `team.name` | token non-expiring; response `ok:false` on error even with 200 |
| notion | `https://api.notion.com/v1/oauth/authorize` | `https://api.notion.com/v1/oauth/token` | (none; `owner=user`) | `workspace_name` | Basic-auth token exchange; rotating refresh on new API versions; pin `Notion-Version: 2025-09-03` |
| confluence | `https://auth.atlassian.com/authorize` (`audience=api.atlassian.com&prompt=consent`) | `https://auth.atlassian.com/oauth/token` | `read:confluence-content.all read:confluence-space.summary search:confluence offline_access` | accessible-resources site `name`/`url` | rotating refresh (90-day inactivity expiry); all API via `api.atlassian.com/ex/confluence/{cloudId}` |
| zendesk | — (token auth) | — | — | — | Basic `email/token:api_token`; incremental articles API 10 req/min |

Env credentials (already in `core/config.py`): `settings.{google,microsoft,slack,notion,
confluence}_client_id/_client_secret`, `settings.slack_signing_secret`,
`settings.inbound_email_domain`.

## Integration rules

- **No new dependencies** — everything above is implementable with httpx, aiosmtplib, stdlib
  (`imaplib`, `email`, `hmac`, `hashlib`, `base64`). If you believe something is impossible
  without a dep, STOP and report; do not add one.
- Migration for the two new tables is written by the ORCHESTRATOR after agents land. Do not
  create alembic files. (Current single head: `a888d11bd47d`.)
- SQLite AND Postgres must both pass. Use `app.core.db` portable types only.
- Verify before finishing: `cd backend && uv run ruff check app tests && uv run ruff format
  --check app tests && uv run mypy app && uv run pytest tests/<your area> -q` (BE agents);
  `cd frontend && pnpm tsc --noEmit && pnpm vitest run <your area>` (FE-D). Fix what you broke,
  including pre-existing tests.
- Never log tokens/secrets. Encrypt at rest via `app.core.security.encrypt_secret`.
- Match the surrounding code style; docstrings explain constraints, not narration.
