---
title: Product tours
description: Record, edit and deliver product tours — steps, triggers, audiences, the Chrome recorder extension, and playing tours via the widget or the AI agent.
---

Tours are in-product walkthroughs played as an overlay on your own site by the widget
loader. Kinds: `flow` (multi-step walkthrough), `banner`, `announcement`. Status walks
`draft → live → paused`.

## Steps

Step types: `tooltip`, `modal`, `banner`, `hotspot`, `action`, `wait`.

```json
{
  "type": "tooltip",
  "selector": "#new-invoice",
  "fallback_selectors": ["[data-testid=new-invoice]"],
  "title": "Start a new invoice",
  "body": "Click **New invoice**.",
  "placement": "bottom",
  "advance": { "on": "element_click" }
}
```

- **Selectors**: `tooltip`/`hotspot`/`action` steps anchor to a CSS `selector`, with up to
  5 `fallback_selectors` and a captured DOM descriptor (`target`) from the recorder for
  self-healing when the primary selector breaks.
- **Advance rules**: `button` (Next button), `element_click`, `input`, or `delay` with
  `delay_ms`.
- `action` steps perform something for the user (`click | fill | navigate`); `wait` steps
  block until an element appears or the URL matches, with a `timeout_ms`.
- Bodies are markdown; steps can carry media, CTAs and a per-tour theme.

## Delivery: triggers, audience, frequency, priority

- **`url_match` trigger** — auto-delivered: the widget asks for eligible experiences on
  boot and on every SPA URL change, and picks live tours whose `url_pattern` glob matches
  the page (max 5 candidates, highest `priority` first).
- **`manual` trigger** — never auto-delivered; played only via `Stept('startTour', id)` or
  by the AI agent.

A matching flow tour is **offered, not forced**: by default
([`tourAutostartPolicy: 'ask'`](/product/widget/#settings)) the widget shows a compact
offer pill the visitor can accept or dismiss. `'auto'` restores the old
play-immediately behavior, `'never'` suppresses autostart entirely. Banners and
announcements always show regardless of the policy — only flow tours are gated.

Per-contact **frequency** controls repeats: `once`, `until_completed`, `until_dismissed`
(default), or `every_time` (optional `cooldown_hours`). **Audience** is `all` or contact
filters, and an optional schedule window bounds delivery in time.

## Publish, pause, preview

`POST /tours/{id}/publish` makes a tour live; `/pause` stops delivery without losing
stats. To check a draft on the real page, mint a preview token
(`POST /tours/{id}/preview-token`, valid 1 hour) and open:

```
https://your-app.example.com/some/page#stept-preview=<token>
```

Preview ignores status, trigger and frequency.

## Recording with the Chrome extension

1. **Install the extension.** The dashboard's extension page (**Tours → connect
   recorder**) links the download. Until the Chrome Web Store listing ships, load it
   unpacked: download the zip from your dashboard (self-hosters can build it with
   `pnpm --filter @stept/extension zip`), then `chrome://extensions` → Developer mode →
   **Load unpacked**.
2. **Connect it.** The same dashboard page mints an extension token (30 days, requires
   `tours:manage`); paste it into the extension to sign in.
3. Record the flow in your real product — clicks and inputs are captured with robust
   selectors; secrets are redacted; screenshots are attached per step.
4. Saving creates a **draft** tour. Edit steps in the dashboard, then publish.

## Stats and events

The widget reports six events: `started`, `step_viewed`, `step_blocked`, `completed`,
`dismissed` and `step_error`. `GET /tours/{id}/stats` aggregates starts/completions;
`GET /tours/{id}/events` lists the raw events — `step_error` tells you which selector
broke where, and `step_blocked` means the step's anchor genuinely wasn't on the page and
the visitor was shown the can't-find-it card (visitors piling up on one `step_blocked`
step usually means that step needs a `url` or a better trigger).

## Playing tours

- **Automatically** — live `url_match` tours are offered via the widget when the URL
  matches (played immediately only with `tourAutostartPolicy: 'auto'`).
- **Programmatically** — `Stept('startTour', '<tour_id>')` from your own code.
- **By the AI agent** — with page control enabled, the agent's `find_guide` tool searches
  live tours (name, description, step text, URL triggers) and `show_guide` plays the match
  in the visitor's page; when no tour exists it can compose an ad-hoc `show_steps`
  walkthrough instead.
- **From MCP** — `browser_run_tour` replays a tour in a connected Chrome via the extension,
  useful for testing that a tour still works (see [MCP](/integrations/mcp/)).
