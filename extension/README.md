# Stept Tour Recorder (Chrome MV3 extension)

Record a product tour by clicking through your app. The extension captures a
robust, stable CSS selector for each clicked element and saves the tour to Stept
as a **draft** via the public recorder endpoint.

## Build

```bash
pnpm --filter @stept/extension install   # first time (deps are in the workspace store)
pnpm --filter @stept/extension build
```

This emits a loadable, unpacked extension into `extension/dist/`:

```
dist/
  manifest.json
  index.html            # popup
  assets/index.js       # popup bundle (Preact)
  assets/index.css      # popup styles
  content.js            # content script (injected on demand)
```

## Load unpacked in Chrome

1. Open `chrome://extensions`.
2. Toggle **Developer mode** on (top-right).
3. Click **Load unpacked** and select the `extension/dist/` folder.
4. Pin **Stept Tour Recorder** from the puzzle-piece menu for easy access.

After each `pnpm --filter @stept/extension build`, click the **reload** icon on
the extension card in `chrome://extensions` to pick up changes.

## Use

1. In the Stept dashboard, open **Tours → Connect recorder** and copy the
   recorder token (a short-lived "recorder" JWT, valid 7 days).
2. Open the extension popup and paste the token. Set **API base** if your
   backend is not at `http://localhost:8600`. Both are saved via
   `chrome.storage`.
3. Navigate to your app's page, click **Start recording**, then click the
   elements you want in the tour. Each click adds a step (with the element's
   text pre-filled as the title).
   - Captured steps persist in `chrome.storage`, so it is fine if the popup
     closes when you click the page — reopen it to see the running list.
   - If you navigate to another page mid-recording, just reopen the popup; it
     re-arms the current page automatically.
4. Edit each step's **title** / **body**, reorder with ↑/↓, or delete.
5. Give the tour a **name** (the **URL pattern** is prefilled from the recorded
   page; edit or clear it).
6. Click **Save tour**. On success the popup shows an **Open in Stept** link to
   the new draft tour. A `401` means the token is invalid or expired — get a
   fresh one from the dashboard.

## Selector strategy

`src/selector.ts` (pure, unit-tested) computes each selector, most-stable first:

1. `[data-tour="…"]` — Stept's dedicated, author-controlled hook.
2. `#id` — when present and unique.
3. A shortest **unique path** of `tag` + `:nth-of-type(n)` segments, anchored on
   the nearest ancestor with a unique `data-tour`/`id`.

Uniqueness is verified at every step with `querySelectorAll(sel).length === 1`.
Identifiers are escaped with a bundled `CSS.escape` implementation. Class names
are intentionally avoided (framework/utility classes are unstable).

## Test

```bash
pnpm --filter @stept/extension test -- --run
```

Vitest + jsdom cover the selector generator (data-tour preference, id, escaping,
`:nth-of-type` disambiguation, uniqueness) and the save-payload builder. The
chrome-dependent code is kept thin and is not unit-tested (no `chrome.*` in
jsdom).

## Notes / limitations

- Permissions are `activeTab`, `scripting`, `storage` only. The content script is
  injected on demand into the active tab; there is no broad host permission.
- The save request works cross-origin because `/api/widget/*` sends wildcard
  CORS, so no host permission is needed for the API call.
- Recording does not block the page's own click handlers, so you can keep
  navigating; a click captured immediately before a full page navigation may
  occasionally be missed.
```
