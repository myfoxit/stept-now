# Old stept extension — capability & architecture map (port source for Wave 7 B4/A3)

Source repo: `/Users/ahoehne/repos/stept` (extension at `extension/`, packages at `packages/`).
Richest of the three candidate repos (58 files / ~10.1k LOC vs ~4k in `stepped`*). Ignore the
worktree copy at `/Users/ahoehne/repos/stept/.claude/worktrees/agent-power-parity/`.

## Overview

- WXT `^0.19` + React 19 + lucide-react; `srcDir: 'src'`, `outDir: 'output'`; workspace deps
  `@stept/compiler`, `@stept/dom-capture`, `@stept/replayer-core`, `@stept/schema`.
- MV3, `minimum_chrome_version: 116`. Permissions: `storage, tabs, scripting, sidePanel,
  activeTab, downloads, webNavigation, debugger, alarms, identity`; host `<all_urls>`.
- Content scripts: (1) `executor.js`, `guide.js`, `redaction.js` — all_urls, top frame,
  document_idle; (2) `recorder.js` — all_urls, **all_frames, document_start**.
- **Side panel** UI (no popup). Command `Ctrl/Cmd+Shift+S`. Badge = step counter.

## Entry points & modules

- `src/entrypoints/background.ts` (1144): session anchor, message router, screenshot capture +
  upload, webNavigation/downloads/tabs observers, auth, save pipeline, guided-replay engine,
  keepalive alarm (0.5 min).
- `src/entrypoints/recorder.content.ts` (478): capture-phase recorder (see below).
- `src/entrypoints/executor.content.ts` (549): in-page RPC island (resolve/describe/set-value/
  select/set-checked/extract/wait-condition/dom-settle/hit-test/scroll-at/…).
- `src/entrypoints/guide.content.ts` (549): guided-replay overlay (see below).
- `src/entrypoints/redaction.content.ts` (214) + `redaction-core.ts` (300): "Smart Blur" PII
  panel — categories emails/names/numbers(4+)/formFields/longText/images as CSS filters via
  `data-stept-redacted`, applied before screenshots, reapplied on navigation.
- `src/entrypoints/sidepanel/`: `Sidepanel.tsx` (293) + components `HomePanel` (231),
  `StepCards` (168), `SettingsDrawer` (167), `Login` (147), `GuidePanel` (117),
  `ProjectChooser` (102), `SaveSheet` (93), `SavedBanner` (84).
- Support: `executor-extension.ts` (980, CDP executor), `drive-controller.ts` (659),
  `run-client.ts` (344, WS), `stept-auth.ts` (200, PKCE), `stept-api.ts` (136), `messages.ts`
  (195), `canvas-typing.ts`, `overlay-priority.ts`, `coord.ts`, `capture-hold.ts`,
  `dom-snapshot.ts` (+ vendored rrweb-snapshot), `secret-redaction.ts`, `snapshot-index.ts`,
  `dom-settle.ts`, `driven-events.ts`, `install-probe.ts`, `device-name.ts`. 17 vitest suites +
  3 Playwright e2e specs.

## State

`chrome.storage.session`: recstate (full raw-event state), rectabs, guidesteps, tokens/PKCE.
`chrome.storage.local`: pairing token, serverUrl, steptUrl, user, refresh token, project ids,
remoteControl, redactionSettings.

## Recording

- Events (capture-phase, passive): `pointerdown` (mints token `pc-<ts>-<rand>`, triggers
  pre-action screenshot + target build), click/dblclick/contextmenu, `input` (debounced 400ms,
  flush on blur/Enter/click), `change` → select/check/upload, `keydown` combos, throttled
  scroll (600ms, top frame), `mouseover` hover-reveal (260ms MutationObserver armed only on
  aria-haspopup/expanded/summary/menu-role/tr — emits only if a menu surface appeared),
  post-action mutation digest (1400ms) → verification effects, canvas typing tracker.
  Background adds `webNavigation.onCommitted` → nav (typed|link|auto + redirect), downloads,
  tab open/close. SPA: popstate + 400ms href poll, deduped vs webNavigation within 1500ms.
- **Screenshot pairing**: `captureVisibleTab(windowId, jpeg q92)` fired at PRE-capture
  (pointerdown), held per-tab keyed by token (`capture-hold.ts`), attached to the event after
  upload; throttled 500ms (350ms pre-capture) against Chrome's 2/s quota; invalidated on
  navigation commit. No cropping — click marker drawn at render time from clickPoint + bbox.
- **Secret redaction** (`secret-redaction.ts`): field-level (password/cc/cvc/otp/api-key attrs)
  + value regexes (sk-, pk_/rk_/ak_, ghp_, xox*-, AKIA, ya29., AIza, JWT, Bearer) + entropy.
  Redacted values never leave the page.
- Iframes: all_frames + `framePath()` walking `window.frameElement` → `FrameRef{selector,
  name, urlPattern}` per hop. Shadow DOM: `composedPath()[0]`, `shadowPathOf()` host chain,
  `pierce/` selectors, `interactiveAncestor()` promotes inner spans to their control.

## Raw events → steps (compiler, `packages/compiler/src/index.ts`, 1092, pure)

`compile(recording, opts) → {workflow, suggestions, warnings, stepSources}` passes:
`orderEvents` (t + causalRank — sendMessage has no ordering guarantee) → typing coalescence →
dblclick collapse → Enter fold (`pressEnterAfter`) → hover dedupe → recorded-gap waits →
`dropSyntheticSubmitClicks` → `reorderDownloadsAfterTrigger` → goal/variable inference →
`deriveTitle`/`slugify`. `stepSources[stepId]=rawEventIndexes` powers panel step deletion.
Old step types (16): navigate, click, type, keypress, select, setChecked, upload, hover,
scroll, waitFor, extract, download, humanGate, agentTask, subworkflow. Steps carry effects
(url-changed/dom-appeared/text-appeared/…) + waitBefore + healing config + optional/disabled.

## dom-capture (packages/dom-capture/src/) — THE crown jewel (ported by A3)

- `selectors.ts` `generateSelectors(el)` ranked stack: `[data-testid|test|cy|qa]` 0.95 →
  stable `#id` 0.90 (blacklists React :r0:, radix-, headlessui-, mui-N, ng-tns-, css-hash,
  uuid/hex/counters) → id-stem `tag[id^=]` 0.62 → `aria/Name[role]` 0.85 (→0.50 volatile) →
  stable-prefix attr 0.76 → `[name=]` 0.80 → `text/Visible` 0.70 (→0.45 volatile) →
  container-anchored 0.72 → minimal css path w/ :nth-of-type 0.60 → xpath 0.30.
  `looksLikeVolatileText` demotes counts/times/currency/dates/numeric tokens.
- `capture.ts` `buildTarget(el)` → `Target{selectors[], text{content,exact}, aria{role,name},
  hints{container,position}, fingerprint, frame[], shadowPath[], bbox{x,y,w,h,viewport}}`;
  `containerHintOf` (fieldset legend/landmark/section heading), `positionHintOf` ("2 of 5").
- `fingerprint.ts`: FNV-1a-64 `elementHash` (tagPath|attrs|axName) / `stableHash` (identity
  attrs only) / `structuralHash` (structure only) + tagPath, attrs, axName, neighborText[≤3].
- `resolve.ts` `resolveTarget(doc, target)` cascade: L0 primary (verified) → L1a ranked
  fallbacks → L1b hashes → L1c tag+axName + container/position disambiguation → L1d unique
  static attr → L2 structural score (role+name .35, text .25, attrs .20, tagPath .15, geometry
  .05 + capped context bonus; needs ≥0.80 and ≥0.10 margin). Levenshtein-graded similarity.
  `targetFragility()` lints position-only targets. `roleFamilyConflict` blocks button↔link.

## Guided replay ("show me" viewing mode) — ports nearly as-is

`guide-core.ts` (pure) + `guide.content.ts` (overlay) + background engine + GuidePanel.
- Overlay in open shadow root, z 2147483645, `pointer-events:none` veil (page stays clickable),
  SVG mask spotlight hole (pad 6), pulsing indigo ring (#6366f1, 1.6s), 296px tooltip card
  (brand header, step counter, progress bar, instruction, copyable value chip, Back/Next/Skip/
  Finish, hint dot). Dark mode + reduced motion.
- `placeTooltip(anchor, tip, viewport, gap=12)`: below → above → floating bottom-center;
  `trackPosition()` re-glues on rAF at >0.5px delta.
- `resolveLoop()` polls every 400ms up to 10s → `notfound` (panel offers Skip) but KEEPS
  polling, emits `found` on late appearance; 900ms watchdog re-resolves after SPA swaps.
- Completion detection `actionSpecFor`: click→pointerdown, type→input+Enter/focusout,
  select/check/upload→change (+pointerdown fallback), keypress→keydown combo, hover→700ms
  pointerover, scroll→IntersectionObserver@0.6, else manual. `hits()` matches through
  `interactiveAncestor` both directions.
- Nav races: `advance` sent BEFORE success flash (beats unload); background auto-advances when
  a committed URL satisfies the step's recorded url effect; re-presents current step
  idempotently on onCompleted/onHistoryStateUpdated. State survives panel close (background +
  storage.session journal). Secrets stripped before payload enters the page.

## Drive mode (browser driving) — CDP

- `drive-controller.ts` + `executor-extension.ts`: trusted input via `chrome.debugger` CDP
  (`Input.dispatchMouseEvent`, `Input.insertText`, `Input.dispatchKeyEvent`,
  `Runtime.evaluate`); 17 remote ops (open/snapshot/act/navigate/scroll/key/wait/capture/
  page-text/find/eval/console/network/resize/back/forward/close); act kinds click/double/
  right/triple-click/hover/type/select/check/uncheck/drag.
- `coord.ts` maps screenshot px → CSS px (absorbs DPR+zoom); `verifyCoordinateHit()` hit-tests.
- Waiting: `settleIdle` + `domSettle()` MutationObserver quiet-window (200ms quiet / 1800ms
  cap) — no blind sleeps. `overlay-priority.ts` lists open-modal controls first.
  Popup following (OAuth windows), LIFO opener stack. `snapshot-index.ts` re-binds indexes.
- Replay loop `packages/replayer-core/src/runner.ts` `runWorkflow()` (853) — `locate()`:
  resolve → confidence ≥ STRONG → hit; ≥ ACTUATE → bank weak hit, poll WEAK_HOLD_MS for
  stronger; else heal-or-emit; deadline → best weak or StepError; LOCATE_POLL_MS between.
  L3 healing = server LLM over WS (omit in port — no server healer in stept-now v1).
- WS transport (`run-client.ts`) to `{server}/ws/extension?token=` — OMIT in port (local
  driving only; remote agentic driving is future scope).

## Old auth (replaced in port)

OAuth PKCE (`/api/v1/auth/authorize` + token exchange) OR pairing code → long-lived automation
pairing token; silent refresh single-flight; `tryAutoRepair()` on 401/403. Port the retry/
single-flight PATTERNS onto the new email/password → extension-token flow.

## Old API surface (replaced): POST /api/assets {dataBase64,mime}→{sha}; POST /api/recordings
(save w/ title overrides/order); GET /api/workflows[/:slug]; WS /ws/extension.
New equivalents: `/api/widget/dap/*` per docs/DAP2-CONTRACTS.md.

## Porting tiers (from source-level review)

- **Tier A (near verbatim)**: dom-capture (selectors/fingerprint/resolve/capture/util),
  guide-core.ts, guide.content.ts, recorder.content.ts event capture, secret-redaction,
  redaction-core, dom-settle, coord, capture-hold, overlay-priority, snapshot-index,
  driven-events, canvas-typing + their vitest suites.
- **Tier B (adapt)**: compiler (re-emit to TourStep v2), background.ts (new API layer, keep
  orchestration/observers/screenshot pipeline/guide engine), stept-auth patterns, sidepanel
  React components (re-skin to new flows).
- **Tier C (subset/omit)**: executor-extension (keep CDP act/type/settle subset for local
  drive), replayer-core runner (keep locate loop), run-client WS (omit), rrweb dom-snapshot +
  sandbox viewer (omit v1), docs-bridge (dead).
