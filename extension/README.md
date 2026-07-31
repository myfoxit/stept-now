# Stept Recorder (Chrome MV3 extension)

Record a product tour by using your app, edit it in the side panel, save it to
Stept as a draft — then replay it two ways: **guide mode** spotlights each step
and waits for you to do it, **drive mode** does it for you.

Built with [WXT](https://wxt.dev) 0.20 + React 19. The element engine is the
shared `@stept/dom-capture` package — the same ranked selectors, fingerprints
and healing cascade the widget's tour player uses, so what the extension records
is exactly what the player can find.

---

## Build & load

```bash
pnpm --filter @stept/extension build       # → extension/dist/chrome-mv3
pnpm --filter @stept/extension dev         # watch mode, auto-reload
```

1. Open `chrome://extensions` and turn on **Developer mode**.
2. **Load unpacked** → select `extension/dist/chrome-mv3`.
3. Pin **Stept Recorder** from the puzzle-piece menu.

Clicking the toolbar icon opens the **side panel** (there is no popup — the panel
survives page navigation, which is what makes multi-page recording work).
`Ctrl+Shift+S` / `Cmd+Shift+S` starts and stops recording without opening it.

After a rebuild, hit **reload** on the extension card to pick up changes.

The toolbar icons are generated, not hand-drawn — `node scripts/gen-icons.mjs`
re-renders `src/public/icon/{16,48,128}.png` from the accent colour.

---

## Sign in

The extension uses your **real Stept account**:

1. Enter your Stept URL (default `http://localhost:8600`), email and password.
2. Pick a workspace — only workspaces where you have `tours:manage` are offered.
3. The extension exchanges your session for a **workspace-scoped extension
   token** (`POST /api/v1/w/{ws}/tours/extension-token`, 30-day TTL) and stores
   only that.

**What is stored:** `{apiBase, extensionToken, workspaceId, workspaceName,
userName}` in `chrome.storage.local`, under a single key. The password and the
short-lived access token are discarded the moment the extension token is minted
and are never written to disk. Every API call re-validates your membership and
`tours:manage` server-side, so removing you from the workspace kills the token
immediately — no revocation step needed.

If the token expires or is revoked, the panel drops to the sign-in screen with an
explanation rather than failing a save silently.

**Advanced → paste a token** accepts an extension token or a legacy recorder
token, for kiosk machines and test harnesses.

---

## Record → save → edit → publish

1. Open the page you want the tour to start on, then **Start recording**.
2. Use your app normally. Every meaningful gesture becomes a step, live in the
   panel:

   | you do | you get |
   | --- | --- |
   | click / double-click / right-click | `tooltip` step, advances on the element click |
   | type into a field | `action {kind:"fill"}` step, advances on input |
   | type into a **password / API-key** field | `tooltip` "enter your own" — the value is never recorded |
   | pick from a `<select>` | `tooltip`, advances on input |
   | check a box, choose a file | `tooltip`, advances on the element click |
   | hover something that opens a menu | `tooltip` (a passive hover is not a step) |
   | navigate / change route | `wait {for:"url"}` step |
   | scroll, download, open a tab | recorded as context, absorbed at compile time |

   Screenshots are captured at **pointerdown** — before your click repaints the
   page — and uploaded as you go, so each card shows the state you acted on.

3. Rename a step inline, edit its description, reorder with ↑/↓, or delete it.
   Deleting removes the underlying raw events, so the step never comes back.
4. **Save tour** creates a **draft** with a suggested name and URL pattern, and
   shows an **Open in Stept** link. Set targeting, polish the copy and publish in
   the dashboard.

The panel also lists the workspace's tours. The pencil icon **pulls** one in for
a quick step edit and pushes it back with `base_version`; if someone edited the
same tour in the dashboard meanwhile, the push is rejected with a friendly
"reload it" prompt instead of clobbering their work.

---

## Guide mode vs drive mode

**Guide (▶)** — the tour is spotlighted on the live page: a dimmed veil with a
hole punched over the target, a pulsing ring, and a coach-mark card with the step
counter and progress bar. The veil never intercepts clicks, so the page stays
fully usable — including the very click you are being asked to make. The overlay
advances when it *observes* you do the step. If an element cannot be found it
offers **Skip** after 10 seconds but keeps looking, and lights up if it appears
late (SPA route transitions do this constantly).

**Drive (🤖)** — Stept performs the tour on the current tab. Action steps are
executed for real, tooltip steps are shown briefly and auto-advance, wait steps
are honoured. Controls: play/pause/stop, a **0.5× / 1× / 2×** speed dial, a live
per-step status list, and on failure a **Retry / Skip / Abort** prompt.

Drive mode is entirely **local** — nothing outside this browser can command it.

### The debugger banner (drive mode caveat)

Trusted input — the kind a page cannot tell from a human — is only available to
an extension through `chrome.debugger` (CDP `Input.dispatchMouseEvent` /
`Input.insertText`). While drive mode runs, Chrome shows a yellow
**"Stept Recorder started debugging this browser"** bar. That is expected; it
disappears when the run ends.

If you dismiss the bar, another debugger client (DevTools) already owns the tab,
or the page is a restricted URL, drive mode falls back to **synthetic events**
(`el.click()`, a React-aware value set + `input`/`change`). The panel says so.
Every step still executes; a small number of apps reject untrusted events.

---

## Selector picker

**Settings → Pick an element** turns the cursor into a picker: hover to highlight,
click to capture. The captured element goes through the same `buildTarget` the
recorder uses, so the primary selector, fallbacks and text hint shown in the panel
are exactly what a recorded step would carry. Copy them to re-target a step by
hand.

---

## Layout

```
src/
  types.ts                    step schema v2 + raw events + panel state (wire contracts)
  messages.ts                 content ↔ background ↔ panel message contracts
  secret-redaction.ts         field- and value-level credential masking (ported)
  capture-hold.ts             pre-capture ↔ event pairing by gesture token (ported)
  dom-settle.ts               MutationObserver quiet-window wait (ported)
  url-pattern.ts              fnmatch-compatible globs + url-pattern suggestion
  api/client.ts               login, token mint, /api/widget/dap/* client
  api/session.ts              the one storage key that holds the extension token
  compiler/                   raw events → TourStep v2 (pure, heavily tested)
  dom/resolve-step.ts         step → element, via the shared dom-capture cascade
  guide/guide-core.ts         guide-mode pure logic (ported)
  driver/{locate,cdp,runner}  drive mode: confidence banking, CDP input, run loop
  entrypoints/
    background.ts             session anchor, capture + save pipelines, engines
    recorder.content.ts       capture-phase recorder (all frames, document_start)
    guide.content.ts          spotlight overlay (top frame)
    driver.content.ts         drive-mode RPC island (top frame)
    picker.content.ts         selector picker (top frame)
    sidepanel/                React side panel
```

---

## Test

```bash
pnpm --filter @stept/extension test        # vitest + jsdom
pnpm --filter @stept/extension typecheck
```

92 unit tests cover the pure modules — every compiler pass (causal ordering,
typing coalescence, double-click collapse, Enter folding, synthetic-submit
dropping, hover folding, titling, URL-pattern suggestion), the step emission
shape for each event kind, the secret-redaction matrix, guide-mode geometry and
completion rules, the driver's locate decisions against a faked executor, and
step→element resolution in jsdom.

`chrome.*` code (entrypoints) is deliberately kept thin and is not unit-tested;
the Playwright extension harness is out of scope for this wave.

---

## Notes & limitations

- **Permissions** are broad by necessity: `<all_urls>` (record any app),
  `debugger` (trusted input for drive mode), `webNavigation` + `downloads`
  (observe what the page does), `alarms` (keep the MV3 worker alive mid-run).
- **Cross-frame:** the recorder runs in every frame and records the frame path,
  so a step inside a same-origin iframe is captured correctly. Guide and drive
  currently resolve in the top document only.
- **Orphaned screenshots:** uploads happen during recording, before a draft
  exists. Abandoning a recording leaves unreferenced images (accepted for v1).
- **Recording never blocks the page's own handlers**, so you can keep navigating;
  a click captured immediately before a full page navigation may occasionally be
  missed.
- **No AI healing.** Resolution is the deterministic L0–L2 cascade only; there is
  no server-side LLM healer to escalate to.
