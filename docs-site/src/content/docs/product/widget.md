---
title: Chat widget
description: Embed the Stept messenger on your site — configuration, identity verification, the command API, and the in-app AI assistant with page control.
---

The widget is a small loader script plus an iframe app. Your widget inbox's settings page
shows the exact snippet with your key filled in:

```html
<script>
  window.SteptSettings = {
    workspaceKey: "wk_…",                    // your widget inbox key
    apiBase: "https://app.stepped.ai",       // optional; defaults to the loader script's origin
    // identify logged-in users (HMAC-verified — see below):
    // identity: { external_id: "user-123", email: "ada@example.com", name: "Ada", hash: "…" },
    // extra origins the AI assistant may navigate to (own origin always allowed):
    // aiAllowedOrigins: [],
  }
</script>
<script src="https://app.stepped.ai/widget-assets/loader.js" async></script>
```

## Command API

The loader installs a global `Stept(...)` function with an Intercom-style pre-load queue
(calls made before the script loads are replayed):

```js
Stept('open')            // open the messenger
Stept('close')           // close it
Stept('toggle')
Stept('show')            // show / hide the launcher
Stept('hide')
Stept('shutdown')        // tear the widget down (e.g. on logout)
Stept('startTour', id)   // play a published tour by id
```

## Identity verification

Anonymous visitors get a generated visitor id. For logged-in users, pass `identity` with an
HMAC so nobody can impersonate another user:

```
hash = HMAC-SHA256(identity_secret, external_id)   // hex, computed on YOUR server
```

Fetch the workspace's `identity_secret` once with
`GET /api/v1/w/{workspace_id}/identity-secret` (requires the `workspace:manage`
permission) and keep it server-side. Verified contacts are flagged `hmac_verified`. Set
`require_identity` in the widget inbox config to refuse anonymous visitors entirely.

## How conversations are created

1. The widget calls `POST /api/widget/boot` with the widget key (+ identity if provided)
   and receives a scoped widget token. All further calls send it as `X-Widget-Token`.
2. Sending the first message calls `POST /api/widget/conversations` — this reuses the
   contact's latest non-resolved conversation on that inbox, or creates a new one.
3. Replies, read receipts and typing indicators go through
   `/api/widget/conversations/{id}/…`; the widget listens for realtime updates over a
   WebSocket (`/ws/widget?token=…`), so agent replies appear instantly.

If the inbox has published help-center articles, the widget also shows a **Help** tab
(see [Help center](/product/help-center/)).

## The in-app AI assistant (page control)

When the inbox's AI agent has **page control** enabled, the agent can see and operate the
page the visitor is on — the tools run in the host page DOM, brokered through the widget.

Consent model, in increasing order:

1. `page_control.enabled` on the agent — unlocks **read-only** tools: `page_snapshot`,
   `page_find`, `page_read`, `page_scroll`, `page_wait`, plus the guide tools
   `show_guide` (plays a published tour) and `show_steps` (an ad-hoc walkthrough, max 8
   steps).
2. `page_control.allow_actions` on the agent **and** the visitor ticking the consent
   checkbox in the messenger — only then do the mutating tools `page_act` (click/type) and
   `page_navigate` exist. Consent is stored per conversation; tools the agent isn't
   entitled to are never offered to the model at all.

Hard limits, regardless of consent:

- **Password fields are never typed into or read.**
- Navigation is same-origin only, unless you allowlist origins via `aiAllowedOrigins`.
- Mark any element `data-stept-no-ai` to make it invisible and untouchable to the agent.
- At most **12 mutating operations** per agent run.
- The widget's own UI is invisible to the agent.

Each operation round-trips through the backend (the run pauses as `awaiting_client`, the
widget executes, posts the result back, the run resumes) — so every page action appears in
the run's step trace like any other tool call.
