---
title: Chat widget
description: Embed the Stept messenger on your site — configuration, identity verification, the command API, and the in-app AI assistant with page control.
---

The widget is a small loader script plus an iframe app. Your widget inbox's settings page
shows the exact snippet with your key filled in — the dashboard generates exactly this:

```html
<script>
  window.SteptSettings = { workspaceKey: "wk_..." };
  window.Stept = window.Stept || function () { (window.Stept.q = window.Stept.q || []).push(arguments) };
</script>
<script src="https://your-stept-host/widget-assets/loader.js" async></script>
```

The second line is the pre-load queue stub: `Stept(...)` exists from the first byte of your
page, and calls made before the (async) loader arrives are replayed in order. Keep it —
without the stub, `Stept('action', …)` before load is a `ReferenceError`.

### Settings

All `window.SteptSettings` keys:

| Key | | |
|---|---|---|
| `workspaceKey` | required | Your widget inbox key (`wk_…`). `widgetKey` is accepted as an alias. |
| `apiBase` | optional | API/asset origin. Defaults to the loader script's own origin. |
| `identity` | optional | Identify logged-in users, HMAC-verified — see below. |
| `locale` | optional | Interface language: `'en'` \| `'de'` \| `'fr'` \| `'es'` \| `'it'`. Without it the widget follows the visitor's browser, then switches to whatever language they actually write in. |
| `lockLocale` | default `false` | Pin the interface to `locale` even when the visitor writes in another language (regulated single-language products). |
| `tourAutostartPolicy` | default `'ask'` | What auto-triggered tours do: `'ask'` shows a compact offer pill, `'auto'` plays immediately, `'never'` suppresses autostart. Explicit starts (`Stept('startTour', …)`, the messenger, previews) always play. See [tours](/product/tours/). |
| `aiAllowedOrigins` | default `[]` | Extra origins the AI assistant may navigate to (own origin always allowed). |

## Command API

The queue stub plus the loader give you a global `Stept(...)` function:

```js
Stept('open')            // open the messenger
Stept('close')           // close it
Stept('toggle')
Stept('show')            // show / hide the launcher
Stept('hide')
Stept('boot', settings)  // re-boot with new settings — (re)identify after login
Stept('shutdown')        // tear the widget down (e.g. on logout)
Stept('startTour', id)   // play a published tour by id
Stept('action', def)     // teach the AI assistant one of your app's actions
Stept('removeAction', n) // …and take it back
```

`Stept('boot', settings)` re-runs the boot handshake with a new settings object — the way
to identify a user after they sign in without reloading the page (`shutdown` then `boot`
on logout/login switches sessions cleanly).

`Stept('action', …)` is the [Actions SDK](/product/actions-sdk/) — one line that lets the
assistant run your own functions (invite a teammate, apply a promo code) in the visitor's
browser, behind confirm cards and optional team approval.

## Single-page apps

The widget patches `history.pushState` / `history.replaceState` (restored on
`Stept('shutdown')`) and fires a `stept:locationchange` event on every route change — that
is how URL-triggered tours, campaigns and surveys follow an SPA without full page loads.
You can listen for the event yourself; no router integration is needed.

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

## Content-Security-Policy

On a host page with a strict CSP, allow:

- **The settings script** — the snippet's first `<script>` is inline. Either allow it
  (a nonce, a hash, or `'unsafe-inline'` in `script-src`) or move the two lines into a
  small external file served from your own origin.
- **`script-src`** — the Stept origin, for `loader.js`.
- **`frame-src`** — the Stept origin, for the messenger iframe.
- **`connect-src`** — the Stept origin, `https:` **and** `wss:` (realtime runs over a
  WebSocket).
- **`img-src`** — the Stept origin (avatars, article images, tour media).
- **`style-src`** — the loader injects inline `<style>` elements for the launcher and tour
  overlays, so `style-src` must permit them (`'unsafe-inline'`, or a nonce policy that
  tolerates injected styles).

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
- Mark any element `data-stept-no-ai` to make it invisible and untouchable to the agent;
  `data-stept-mask` masks just the element's content from page capture (the element still
  exists for the agent, its text does not).
- At most **12 mutating operations** per agent run.
- The widget's own UI is invisible to the agent.

Each operation round-trips through the backend (the run pauses as `awaiting_client`, the
widget executes, posts the result back, the run resumes) — so every page action appears in
the run's step trace like any other tool call.
