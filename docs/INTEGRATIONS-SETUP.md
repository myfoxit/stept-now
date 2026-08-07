# Integrations setup — the operator's registration guide

This is the step-by-step guide to registering the OAuth apps and provider accounts that power
Stept's one-click integrations. You do each registration **once per installation**; after that,
every workspace admin gets one-click "Connect" buttons in **Settings → Integrations**.

How credentials flow:

1. You register an app in the provider's console (this doc) and obtain a client id + secret.
2. You either export them as env vars on the API service (instance-wide default) **or** paste
   them in Settings → Integrations → the provider card → "Use your own app" (workspace-scoped,
   overrides env). Cloud tenants can bring their own apps this way.
3. Stept builds the authorize URL server-side, sends the admin through the provider's consent
   screen, and stores the resulting tokens encrypted (Fernet, derived from `STEPT_SECRET_KEY`).

**The one redirect URI rule:** every OAuth provider must be given exactly

```
{STEPT_PUBLIC_BASE_URL}/api/integrations/oauth/{provider}/callback
```

e.g. `https://app.stepped.ai/api/integrations/oauth/google/callback`. One URL serves every
workspace — the workspace identity travels in the signed `state` parameter, never in the URL.
The Integrations page shows the exact per-provider URI with a copy button.

Env var reference (set on the `api` service; all optional — a provider without credentials just
shows "Needs setup" in the UI):

```
STEPT_GOOGLE_CLIENT_ID / STEPT_GOOGLE_CLIENT_SECRET
STEPT_MICROSOFT_CLIENT_ID / STEPT_MICROSOFT_CLIENT_SECRET
STEPT_SLACK_CLIENT_ID / STEPT_SLACK_CLIENT_SECRET / STEPT_SLACK_SIGNING_SECRET
STEPT_NOTION_CLIENT_ID / STEPT_NOTION_CLIENT_SECRET
STEPT_CONFLUENCE_CLIENT_ID / STEPT_CONFLUENCE_CLIENT_SECRET
STEPT_INBOUND_EMAIL_DOMAIN            # e.g. inbound.stepped.ai — enables forward-to addresses
```

---

## 1. Google — Gmail inboxes + Google Drive knowledge <a name="google"></a>

One Google Cloud OAuth app powers both Gmail (send/receive support mail) and Drive (knowledge
sync). Scopes requested: `https://mail.google.com/`, `.../auth/drive.readonly`, `openid email`.

1. Go to **console.cloud.google.com** → create/select a project.
2. **APIs & Services → Enabled APIs** → enable **Gmail API** and **Google Drive API**.
3. **APIs & Services → OAuth consent screen**:
   - **User type**: pick **Internal** if your company uses Google Workspace — this is the
     self-hoster golden path: no verification, no user caps, done in minutes. Pick **External**
     only if the connecting Google accounts are outside your Workspace org.
   - App name, support email, domain — fill honestly; they appear on the consent screen.
4. **Credentials → Create credentials → OAuth client ID → Web application**:
   - Authorized redirect URI: `{PUBLIC_BASE_URL}/api/integrations/oauth/google/callback`
   - Copy the client id + secret → env vars or the Google card in Settings → Integrations.

**The verification reality (read before going "External"):**

| Consent screen state | Consequence |
|---|---|
| Testing | Max 100 test users, **refresh tokens die after 7 days** — unusable beyond a demo |
| In production, unverified | "Unverified app" warning screen; **lifetime cap of 100 users**; fine for a single team that clicks through once |
| Verified with restricted scopes | Required for a public/cloud offering: brand verification + restricted-scope review + **annual CASA Tier 2 security assessment** (~$540–$675/yr via Google's discounted lab; 4–6 weeks) because `mail.google.com` and `drive.readonly` are *restricted* scopes |

Practical guidance: self-hosters use **Internal** (Workspace) or accept the unverified warning
(their own admins are the only users). Only the hosted cloud needs CASA — budget it when cloud
revenue justifies it.

## 2. Microsoft — Microsoft 365 / Outlook email <a name="microsoft"></a>

Scopes: `offline_access openid email IMAP.AccessAsUser.All SMTP.Send` (Outlook resource) —
send + receive through the customer's mailbox over XOAUTH2.

1. Go to **entra.microsoft.com** (Azure portal → Microsoft Entra ID) → **App registrations →
   New registration**.
2. Name it (users see it on consent). **Supported account types**:
   - Self-host, own tenant only: **Single tenant** — zero external requirements.
   - Cloud/multi-org: **Multitenant** — note that users in *other* tenants can only consent if
     you complete **publisher verification** (Microsoft Partner Center account + a DNS-verified
     custom domain; not `*.onmicrosoft.com`).
3. **Redirect URI** (type *Web*): `{PUBLIC_BASE_URL}/api/integrations/oauth/microsoft/callback`
4. **Certificates & secrets → New client secret** → copy the **Value** immediately (it is
   shown once). Note the expiry (max 24 months — calendar a rotation).
5. **API permissions → Add** → *Microsoft Graph* delegated `openid`, `email`,
   `offline_access`, and under **APIs my organization uses → Office 365 Exchange Online**:
   `IMAP.AccessAsUser.All`, `SMTP.Send`. Grant admin consent for your tenant if prompted.
6. Client id ("Application (client) ID") + secret → env vars or the Microsoft card.

Gotchas: the mailbox must have IMAP enabled (Exchange admin → mailbox → email apps) and
**SMTP AUTH** enabled (tenant-level "Authenticated client SMTP submission"); the SMTP login is
the account's UPN — Stept uses the `preferred_username` claim automatically.

## 3. Slack — one-click channel install <a name="slack"></a>

1. **api.slack.com/apps → Create New App → From a manifest**, paste (adjust name/URLs):

```yaml
display_information:
  name: Stept
features:
  bot_user:
    display_name: stept
oauth_config:
  redirect_urls:
    - https://YOUR_HOST/api/integrations/oauth/slack/callback
  scopes:
    bot:
      - channels:history
      - channels:read
      - chat:write
      - groups:history
      - groups:read
      - im:history
      - im:read
      - im:write
      - users:read
      - team:read
settings:
  event_subscriptions:
    request_url: https://YOUR_HOST/api/channels/slack/events
    bot_events:
      - message.channels
      - message.groups
      - message.im
  interactivity:
    is_enabled: false
```

2. **Basic Information**: copy **Client ID**, **Client Secret**, and **Signing Secret** →
   `STEPT_SLACK_CLIENT_ID` / `STEPT_SLACK_CLIENT_SECRET` / `STEPT_SLACK_SIGNING_SECRET` (the
   signing secret verifies inbound event webhooks; without it events are rejected).
3. Slack will verify the events `request_url` with a challenge — Stept answers it automatically
   once deployed.
4. In Stept: Settings → Integrations → Slack → **Connect** → pick the workspace → Stept
   auto-creates the Slack inbox for that team. No manual token pasting.

Listing in the Slack Marketplace (optional, later): app review required; it is also a real
discovery channel.

## 4. Notion — knowledge sync <a name="notion"></a>

Two paths; both are supported by the Notion knowledge source:

- **Internal integration (fastest, per-workspace):** notion.so/profile/integrations → New
  integration → copy the secret token → paste it directly in the knowledge source dialog.
  No OAuth app needed. Users must "connect" their pages to the integration in Notion
  (Share → connections) — only connected pages are readable.
- **Public OAuth integration (one-click for everyone):** in the same console create a
  **Public** integration: redirect URI
  `{PUBLIC_BASE_URL}/api/integrations/oauth/notion/callback`, capabilities: **Read content**
  (nothing else). Copy the OAuth client id + secret → `STEPT_NOTION_CLIENT_ID/SECRET`.
  Users then click Connect and pick pages in Notion's own picker — no token handling at all.
  Review is only required to be listed in Notion's public gallery; an unlisted public
  integration works immediately.

## 5. Atlassian Confluence — knowledge sync <a name="confluence"></a>

1. **developer.atlassian.com → Console → Create → OAuth 2.0 integration**.
2. **Permissions → Confluence API** → add scopes: `read:confluence-content.all`,
   `read:confluence-space.summary`, `search:confluence` (granular consoles: also accept the
   suggested classic equivalents). `offline_access` is requested at runtime automatically.
3. **Authorization** → callback URL: `{PUBLIC_BASE_URL}/api/integrations/oauth/confluence/callback`
   (exact match enforced).
4. **Distribution → Sharing: enabled** — otherwise only the app owner's Atlassian account can
   authorize. Unreviewed apps show a small "not approved by Atlassian" note on consent; that is
   normal and fine for your own installation.
5. Client id + secret → `STEPT_CONFLUENCE_CLIENT_ID/SECRET`.

Notes: tokens are workspace-agnostic until the user picks a site — Stept resolves the
`cloudId` automatically after consent (when the account has several Confluence sites the
knowledge source dialog lets you pick). Atlassian rotates refresh tokens and expires them
after 90 days of *inactivity* — a paused sync longer than that needs a reconnect (the UI will
show "Reauthorize").

No OAuth alternative: a per-user **API token** (id.atlassian.com/manage-profile/security/api-tokens)
+ site URL + account email also works in the source dialog (Basic auth) — zero registration.

## 6. Zendesk — help-center import/sync <a name="zendesk"></a>

No app registration. In Zendesk **Admin Center → Apps and integrations → APIs → Zendesk API**:
enable **Token access** → **Add API token** → copy. In Stept's knowledge source dialog enter
subdomain (`acme` for `acme.zendesk.com`), the admin email, and the token. Stept uses the
incremental articles API (hourly-capable, cursor-based) — first sync imports everything,
follow-ups only pull changes.

## 7. Email sending providers (ESPs) <a name="esp"></a>

Every email inbox picks a transport. What each needs:

| Transport | Credentials to obtain | Inbound path |
|---|---|---|
| **Amazon SES** | IAM user/role access key + secret with `ses:SendEmail` (SESv2); region | SES **receipt rule** → SNS topic → HTTPS subscription to the inbox's SES webhook URL (Stept confirms the subscription automatically) |
| **Resend** | API key (resend.com/api-keys) | Inbound: add the inbox webhook URL under Webhooks (`email.received`), copy the webhook signing secret into the inbox config |
| **Postmark** | Server API token (server → API Tokens) | Server → **Inbound** stream webhook = the inbox webhook URL; or use Postmark's inbound address as your forwarding target |
| **SendGrid** | API key with Mail Send | **Inbound Parse** → add host + URL = the inbox webhook URL (multipart posts) |
| **Mailgun** | Sending API key; domain + region (US/EU) | **Routes** → forward to the inbox webhook URL; copy the **webhook signing key** into the inbox config (HMAC-verified) |
| **SMTP/IMAP** | Host, port, login, password (or app password) | IMAP polling (interval configurable per inbox) |
| **Gmail / Microsoft 365** | The OAuth connections from §1/§2 | IMAP XOAUTH2 polling |

The inbox wizard prints the exact webhook URL (it embeds the inbox id + a secret token) — paste
it into the provider console. **Never share that URL publicly; the token in it is the auth.**

### Deliverability DNS (do this for any custom from-address)

For the domain you send support mail from:

- **SPF**: include your ESP (`v=spf1 include:amazonses.com ~all`, `include:_spf.resend.com`,
  `include:spf.mtasv.net` (Postmark), `include:sendgrid.net`, `include:mailgun.org`, …).
- **DKIM**: create the CNAME/TXT records your ESP's "domain verification" screen shows — send
  from an unauthenticated domain and Gmail/Yahoo will junk or reject it (they enforce DKIM+SPF
  alignment for bulk senders since 2024).
- **DMARC**: at minimum `_dmarc TXT "v=DMARC1; p=none; rua=mailto:dmarc@yourdomain"`; tighten
  to `quarantine` once reports look clean.
- **Forwarding domain**: point `STEPT_INBOUND_EMAIL_DOMAIN` (e.g. `inbound.stepped.ai`) MX at
  your inbound provider (SES receipt, Postmark inbound, Mailgun routes) so auto-generated
  forward-to addresses (`in-…@inbound.stepped.ai`) resolve.

## 8. Channel apps you may also want (already supported, token-paste today)

These channels exist in Stept with pasted credentials; registering the apps is still on you:

- **Meta (WhatsApp Cloud / Messenger / Instagram)**: developers.facebook.com → create a
  **Business** app → add WhatsApp / Messenger / Instagram products. You need: app id + app
  secret (webhook signature verification), a permanent token (System User token for WhatsApp),
  and to point the product's webhook at
  `{PUBLIC_BASE_URL}/api/channels/{whatsapp|messenger|instagram}/webhook/{inbox_id}` with the
  verify token shown in the inbox dialog. Advanced access for `pages_messaging` /
  `whatsapp_business_messaging` requires Meta **App Review** (business verification; plan ~1–3
  weeks) before non-admin users' messages flow. One-click "embedded signup" is a future wave —
  registration is identical, so nothing you do here is throwaway.
- **Twilio SMS**: console.twilio.com → Account SID + Auth Token + a phone number; set the
  number's inbound webhook + status callback to the URLs the inbox dialog shows.
- **Telegram**: @BotFather → `/newbot` → token; paste into the inbox dialog, then run the
  `setWebhook` command the dialog prints (auto-registration is a follow-up).
- **LINE**: developers.line.biz → Messaging API channel → channel id/secret/token; webhook URL
  from the inbox dialog.

## 9. Checklist to "everything one-click" on a fresh install

1. `STEPT_PUBLIC_BASE_URL` + `STEPT_APP_BASE_URL` set to your real HTTPS hosts.
2. Google app (§1) → env vars → Gmail inboxes + Drive knowledge work.
3. Microsoft app (§2) → env vars → M365 inboxes work.
4. Slack app (§3) → env vars → Slack inbox one-click install works.
5. Notion public integration (§4) + Confluence app (§5) → knowledge one-click works.
6. Pick your ESP (§7), set DNS (SPF/DKIM/DMARC), set `STEPT_INBOUND_EMAIL_DOMAIN` + MX.
7. Restart the API; every provider card in Settings → Integrations should now show Connect
   instead of "Needs setup".
