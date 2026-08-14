---
title: Integrations overview
description: OAuth and token-based integrations, per-workspace OAuth credentials, email transports, and inbound email.
---

Integrations connect a workspace to outside systems — email accounts, chat channels and
knowledge sources. Manage them in **Settings → Integrations**.

## OAuth providers

One-click connect flows (browser redirect, tokens stored encrypted per workspace):

| Provider | Used for |
| -------- | -------- |
| Google | Gmail sending/receiving, Google Drive knowledge sources |
| Microsoft 365 | Outlook email inboxes |
| Slack | One-click install — provisions a Slack inbox on connect |
| Notion | Notion knowledge sources |
| Confluence | Confluence Cloud knowledge sources |

Every provider uses a single redirect URI per instance:

```
{STEPT_PUBLIC_BASE_URL}/api/integrations/oauth/{provider}/callback
```

Register that URL in the provider's OAuth app settings.

### Whose OAuth app is used?

Two levels, and the workspace always wins:

1. **Instance credentials** — set `STEPT_GOOGLE_CLIENT_ID` / `STEPT_GOOGLE_CLIENT_SECRET`
   (and the Microsoft/Slack/Notion/Confluence equivalents) in the server environment; every
   workspace shares those apps.
2. **Per-workspace override** — a workspace can store its own client id/secret for a
   provider (`PUT /integrations/{provider}/credentials`). When present it is always used
   instead of the instance credentials.

## Token-based integrations

No OAuth dance — paste credentials directly on the source or inbox (stored encrypted,
never returned):

- **Zendesk** — Help Center sync with your subdomain, email and API token.
- **Telegram** — a bot token on the Telegram inbox.
- **GitHub** — repository knowledge sources with an access token.
- **Notion** — also supports an internal integration token instead of OAuth.

## Email transports (outbound)

Each email inbox picks a `transport` in its config:

`global` (the instance SMTP settings) · `smtp` (per-inbox SMTP server) · `gmail` /
`microsoft` (OAuth-connected account, SMTP XOAUTH2) · `ses` · `resend` · `postmark` ·
`sendgrid` · `mailgun` (provider HTTP APIs).

Each transport validates the secrets it needs (API key, SMTP password, AWS keys + region,
Mailgun domain, …). Threading headers pass through untouched, so replies stay in the same
email thread regardless of transport.

## Inbound email

Two ways to get email into an inbox:

1. **Provider webhooks** — point your ESP's inbound parse at the tokened URL for the inbox:

   ```
   POST /api/channels/email/inbound/{resend|postmark|sendgrid|mailgun|ses}/{inbox_id}/{token}
   ```

   The token is generated per inbox; payload parsing is provider-specific.

2. **Forwarding address** — set `STEPT_INBOUND_EMAIL_DOMAIN=in.yourdomain.com` on the
   instance and every email inbox gets a unique auto-generated forwarding address like
   `in-a1b2c3d4e5f6@in.yourdomain.com`. Forward your support address to it and route the
   domain's inbound mail to Stept via one of the webhook providers above.

## Messaging channels

Slack, Telegram, WhatsApp, Messenger, Instagram, LINE and SMS inboxes receive events via
per-channel webhooks under `/api/channels/…`; each inbox's settings page shows the exact
webhook URL to register with the provider.
