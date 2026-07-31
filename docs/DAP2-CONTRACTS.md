# Wave 7 — DAP2 + Extension + Ingestion + Editor: Binding Contracts

Read `CLAUDE.md` first, then your section. These contracts extend `docs/CONTRACTS.md` (wave 1–6
conventions all still apply: portable DB types, workspace scoping, `require_perm`, events,
`audit.record`, typed `response_model` everywhere, tests are part of the feature).
If reality forces a deviation, implement the closest faithful version and CLEARLY list
deviations in your final report.

**Goal:** stept becomes a full-blown open-source DAP + Intercom combo:
richer tours (step types, do-it-for-me driving, robust selectors), checklists, surveys
(NPS/rating/text), banners/announcements, targeting/frequency/scheduling, real analytics,
a ported full-power Chrome extension (login-based, record + preview + drive), PDF/DOCX/web-crawl
ingestion upgrades, and a TipTap editor for authored content.

---

## Shared decisions (all agents — binding)

1. **Experience kinds, one engine.** `Tour.kind ∈ {"flow","banner","announcement"}` — banners and
   announcement modals are single-step tours reusing tour targeting/analytics. Checklists and
   Surveys are separate models. All DAP admin perms reuse `tours:read` / `tours:manage` (no new
   perms). Widget delivery for ALL kinds goes through one bootstrap endpoint
   `GET /api/widget/experiences`.
2. **Markdown stays the persisted content format** everywhere (article bodies, step bodies,
   checklist item bodies, survey thanks message). TipTap is an EDITING surface that serializes
   to/from markdown — the RAG chunker (`#`-heading logic) and both hand-rolled renderers keep
   working. Never store TipTap JSON/HTML in the DB.
3. **Selector contract** (recorder + player + editor share it): each targeted step carries
   `selector: str` (primary), `fallback_selectors: list[str]` (ordered), `text_hint: str`
   (normalized visible text, ≤80 chars) — the SIMPLE projection — plus an optional rich
   `target` object: the full `@stept/dom-capture` Target descriptor
   `{selectors: [{kind: css|aria|text|xpath|pierce, value, score}], text, aria, hints,
   fingerprint: {elementHash, stableHash, structuralHash, tagPath, attrs, axName, neighborText},
   frame: [FrameRef], shadowPath: [str], bbox}` (opaque JSON to the backend). Players resolve
   via the dom-capture cascade when `target` is present (ranked selectors → fingerprint match →
   structural scoring), else primary → fallbacks → text-hint scan. A step that resolves via
   anything but the primary selector is a **self-heal**; the player reports `step_viewed` with
   `meta.healed=true`. A step that resolves nowhere emits `step_error` with
   `meta.reason="not_found"` and is skipped. The shared engine lives in
   `packages/dom-capture` (ported from the old repo by agent A3) and is consumed by BOTH the
   widget player and the extension — never fork it.
4. **Auth for the extension = the real login system.** Extension logs in with email/password via
   `POST /api/v1/auth/login` (fetched from the extension service worker — host_permissions bypass
   CORS), lists workspaces, then mints a long-lived scoped token:
   `POST /api/v1/w/{ws}/tours/extension-token` → JWT `typ="extension"`, TTL 30d, claims `{ws, sub}`.
   Every extension API call re-validates membership + `tours:manage` server-side (same pattern as
   `authorize_recorder`). The old paste-a-recorder-token flow stays as a fallback.
5. **Public media for tour steps.** New storage namespace + route: uploads via
   `POST /api/v1/w/{ws}/files?public=true` are stored under key prefix `public/{workspace_id}/…`
   and served WITHOUT auth at `GET /api/widget/media/{workspace_id}/{key:path}` (open CORS; only
   keys under `public/{workspace_id}/` are servable; uuid7 keys are unguessable). Non-public files
   behave exactly as today.
6. **Event names** (already stubbed by orchestrator in `app/core/events.py`): existing
   `TOUR_EVENT="tour.event"`, new `CHECKLIST_EVENT="checklist.event"`,
   `SURVEY_SUBMITTED="survey.submitted"`. `record_event`-style service fns also broadcast realtime
   to topic `ws:{workspace_id}` (type = event name) so dashboards can live-update.
7. **No new deps** beyond what the orchestrator pre-installed (already in lockfile): frontend
   has TipTap v3 (`@tiptap/react`, `@tiptap/pm`, `@tiptap/starter-kit`, `@tiptap/extension-image`,
   `@tiptap/extensions`); extension is now a **WXT 0.20 + React 19** package (`wxt`,
   `@wxt-dev/module-react`, `react`, `react-dom`, `lucide-react`) with `srcDir: 'src'`,
   entrypoints in `extension/src/entrypoints/`, build output `extension/dist/chrome-mv3`;
   `packages/dom-capture` is a new workspace package (plain TS, consumed as source). Backend
   gets NOTHING new (pypdf/python-docx/bs4/lxml/httpx already present). Flag gaps in your final
   report instead of adding deps.
8. **Migrations are orchestrator-owned.** Agents change `app/models/*` freely but do NOT touch
   `backend/alembic/`. Dev SQLite is rm+reseed (`make seed`).
9. **Widget ↔ backend glob semantics unified**: backend keeps Python `fnmatch`; the widget's
   `globMatch` becomes case-insensitive and supports `*`/`?` (document `[seq]` as backend-only).
10. **Known bugs to fix in your area** (from recon — each is assigned below): manual-tour
    `startTour` broken; `_resolve` not checking `inbox.enabled`/channel_type; recorder TTL
    duplicated; `history.pushState` patch stacking; tour progress not persisted across reloads;
    `TourEvent.meta` never written.

---

## Wave A — Agent A1: DAP core backend (tours v2, experiences, extension API, analytics)

**Owns:** `app/models/tour.py`, `app/schemas/tours.py`, `app/services/tours.py`,
`app/api/v1/tours.py`, `app/api/widget/tours.py`, `app/api/widget/dap.py` (NEW),
`app/dap/seed.py`, `tests/tours/*`, `tests/dap/*` (new dir). Extends
`app/core/security.py` ONLY by adding `create_extension_token` + widening `TokenType`
(surgical — nothing else in that file), and `app/api/v1/files.py` ONLY for the
`?public=true` flag + `app/api/widget/media.py` (NEW) for public serving.

### Tour model changes (`app/models/tour.py`)
Add columns (all with server-safe defaults so existing rows keep working):
- `kind: str = "flow"` (`flow|banner|announcement`)
- `schedule: PortableJSON = {}` — `{start_at?: iso, end_at?: iso}` (UTC; empty = always)
- `frequency: PortableJSON = {"type": "until_dismissed"}` —
  `{type: "once"|"until_completed"|"until_dismissed"|"every_time", cooldown_hours?: int}`
- `priority: int = 0` (higher first in delivery ordering)
- `settings: PortableJSON = {}` — `{mode: "guided"|"driven" = "guided", backdrop: bool = true,
  show_progress: bool = true, dismissable: bool = true}`
- `theme` stays `{accent}` + optional `{position: "top"|"bottom"}` for banner kind.

### Step schema v2 (`app/schemas/tours.py`) — THE core contract
```
TourStepIn/Out {
  id: str,
  type: "tooltip"|"modal"|"banner"|"hotspot"|"action"|"wait" = "tooltip",
  selector: str = ""            # required for tooltip/hotspot/action, and wait(for=element)
  fallback_selectors: list[str] = [],   # max 5
  text_hint: str = "",
  target: dict | None = None,   # full @stept/dom-capture Target (opaque JSON; ≤8KB; passthrough)
  title: str = "", body: str = "",      # body = markdown
  media: {type: "image"|"video", url: str} | None = None,
  screenshot_key: str | None = None,    # admin preview thumbnail (uploaded by extension)
  placement: "auto"|"top"|"bottom"|"left"|"right"|"center" = "auto",
  advance: {on: "button"|"element_click"|"input"|"delay", delay_ms?: int} = {on:"button"},
  action: {kind: "click"|"fill"|"navigate", value?: str, url?: str} | None = None,  # type=action
  wait: {for: "element"|"url", selector?: str, url_pattern?: str, timeout_ms: int = 10000} | None,
}
```
Validation: `action` required iff `type=="action"`; `wait` required iff `type=="wait"`;
`selector` non-empty for tooltip/hotspot/action; modal/banner ignore selector. `hotspot` = a
pulsing beacon anchored to the element; clicking it opens the tooltip content; advance rules as
tooltip. `_normalize_steps` fills ids/defaults; `_steps_signature` extended to all content fields
(version bump semantics kept; `target`/`screenshot_key` changes do NOT bump version).
`WidgetTourOut` steps include `target` (players need it); gains `kind, settings, frequency_type`
(widget needs `every_time` to bypass its local seen-set) — NEVER expose audience/schedule
internals to the widget.

### Delivery v2 (`services/tours.py`)
`deliverable_tours(session, workspace_id, *, url, contact)`:
- statuses `live` only; kind any; trigger `url_match` fnmatch as today (manual excluded from
  auto-delivery, but see manual start fix below); schedule window check (`start_at<=now<=end_at`);
- frequency (identified contact, from TourEvents): `once` → exclude if any `started`;
  `until_completed` → exclude if `completed`; `until_dismissed` → exclude if `completed` or
  `dismissed` (today's behavior, stays the default); `every_time` → include (respect
  `cooldown_hours` vs latest event `created_at`). Anonymous → today's behavior (widget seen-set
  guards, except `every_time` which always includes);
- audience filters as today; order by `priority desc, created_at asc`; return at most 5.
- **Efficiency fix:** batch the event lookups (one query for all candidate tour ids), and
  `_audience_matches` must stop calling `apply_filters` per tour — evaluate the contact against
  filters directly (add `segments.contact_matches(session, workspace_id, contact, filters) -> bool`
  to `app/services/segments.py` — you own that addition; keep `apply_filters` untouched).
- **Manual start fix:** new `GET /api/widget/tours/{tour_id}?widget_key=` returns a live tour of
  any trigger type (frequency/audience still enforced; 404 otherwise) — the loader's
  `startTour(id)` uses this instead of the list.
- **Preview:** `POST /api/v1/w/{ws}/tours/{id}/preview-token` (`tours:manage`) → JWT
  `typ="tour_preview"`, TTL 1h, claims `{ws, tour}`. `GET /api/widget/tours/{tour_id}` accepts
  `?preview_token=` and then returns the tour regardless of status/trigger/frequency (still only
  that tour id). Widget appends `#stept-preview=<token>` support (B3).
- `_resolve` now checks `inbox.enabled` and `channel_type=="widget"` (404 otherwise) — bug fix.

### Events + analytics v2
- `record_event` accepts `meta: dict | None` (widget sends `{url, viewport_w, healed?, reason?}`)
  and new event name `step_error`. Persist meta. Broadcast `tour.event` to `ws:{workspace_id}`.
- `compute_stats` v2 → `TourStats` gains: `unique_starts` (distinct contact_id, null counted per
  event), `by_day: [{date, starts, completions}]` (last 30d), `step_errors: int`, and per-step
  `healed` count. Keep SQL-side aggregation (GROUP BY) — no full-table Python scans.
- NEW `GET /tours/{id}/events` (`tours:read`) → `OffsetPage[TourEventOut]` (event, step_index,
  contact_id, meta, created_at; newest first).

### Extension API (`app/api/widget/dap.py`, prefix `/api/widget/dap`, open CORS)
Auth helper `authorize_extension(session, request) -> (workspace_id, user_id)`: Bearer token,
`decode_token(token, "extension")` (also accept `typ="recorder"` for back-compat), membership +
`TOURS_MANAGE` re-check per call. Endpoints:
- `GET /tours` → `[{id, name, kind, status, steps_count, version, updated_at}]`
- `GET /tours/{id}` → full `TourOut`
- `POST /tours` `{name, url_pattern?, steps: [TourStepIn]}` → draft `TourOut` (rich superset of
  the old recorder endpoint; keep `POST /api/widget/tours/recorder` working, delegating here)
- `PUT /tours/{id}/steps` `{steps: [TourStepIn], base_version: int}` → `TourOut` (409 on version
  mismatch — recorder edits must not clobber concurrent dashboard edits)
- `PATCH /tours/{id}` `{name?, url_pattern?}` → `TourOut` (drafts only, 409 if live)
- `POST /screenshots` multipart `file` (png/jpeg ≤2MB) → `{key}` — tour-INDEPENDENT upload
  (extension uploads eagerly during recording, before the draft exists), stored PUBLIC under
  `public/{workspace_id}/…`; steps reference the returned key as `screenshot_key` at save time.
  Orphaned screenshots are acceptable v1 (note in docs).
- `POST /auth/check` → `{workspace_id, workspace_name, user_name, perms_ok: true}` (extension
  settings screen validation).
`app/api/v1/tours.py` additions: `POST /tours/extension-token` → `{token, expires_days: 30}`;
recorder TTL single-sourced: `security.RECORDER_TOKEN_TTL_DAYS = 7`, `EXTENSION_TOKEN_TTL_DAYS = 30`
constants imported by the router (fix the 7-in-two-places bug).

### Public media (`app/api/widget/media.py` + files.py flag)
`POST /api/v1/w/{ws}/files?public=true` (member, any allowed content type from the existing list,
images/video only for public: png/jpeg/gif/webp/svg/mp4) → key `public/{workspace_id}/YYYY/MM/…`;
response `url` = `/api/widget/media/{workspace_id}/{key}`. `GET /api/widget/media/{workspace_id}/{key:path}`:
serve only if `key.startswith(f"public/{workspace_id}/")` after normalization (404 otherwise),
correct content-type, `Cache-Control: public, max-age=86400`, no auth. Reject `..` traversal.

### Seed (`app/dap/seed.py`)
Keep existing two tours + events. Add (idempotent by name): 1 banner tour "What's new in Stept"
(kind=banner, one banner step, url `*`, frequency `once`), 1 driven-mode draft flow "Create your
first automation" (action step clicking `[data-tour="automations"]`, wait step, tooltip), and
extra TourEvents incl. one `step_error` + one healed `step_viewed` so analytics render.

### Tests (extend `tests/tours/`, new `tests/dap/`)
Step schema validation matrix (per-type required fields), delivery matrix: schedule window,
each frequency type (incl. cooldown + anonymous), priority ordering, batched-query behavior
(assert ≤3 SELECTs for delivery via query counting? skip if brittle — at least correctness),
manual tour via GET by id + 404 for draft, preview token happy/expired/wrong-tour,
extension-token mint + authorize + recorder back-compat, dap endpoints CRUD + version-conflict
409 + screenshot upload→public serve→traversal rejected, stats v2 math incl. by_day/unique/healed,
events pagination, `_resolve` disabled-inbox 404, media route cross-workspace isolation,
authz everywhere (viewer 403 on manage, cross-ws 404). Existing tests must stay green
(update shapes where the contract legitimately changed).

---

## Wave A — Agent A1b: Checklists + Surveys backend

**Owns:** `app/models/{checklist,survey}.py`, `app/schemas/{checklists,surveys}.py`,
`app/services/{checklists,surveys}.py`, `app/api/v1/{checklists,surveys}.py`,
`app/api/widget/{checklists,surveys}.py`, `tests/checklists/*`, `tests/surveys/*`.
(Registered in registries by orchestrator; experiences bootstrap in A1's
`app/api/widget/dap.py` imports YOUR service fns — signatures below are LOAD-BEARING.)

### Checklist models
- `Checklist`: ws, name, description="", status draft|live|paused, `items` PortableJSON list
  `[{id, title, body: markdown = "", action: {type: "start_tour"|"open_url"|"open_messenger"|"none",
  tour_id?, url?} = none, completion: {type: "manual"|"tour_completed"|"url_visited",
  tour_id?, url_pattern?} = manual}]` (max 20 items), trigger JSON (same shape as tours),
  audience JSON (same), theme `{accent, position: "bottom-right"|"bottom-left"}`,
  launcher JSON `{label: "Getting started", auto_open_once: bool = true}`, priority int=0,
  version int (bump on items change), created_by. 
- `ChecklistProgress`: ws, checklist_id FK CASCADE, contact_id GUID (bare, indexed, NOT NULL —
  anonymous progress lives client-side only), `item_state` PortableJSON `{item_id: iso_completed_at}`,
  dismissed_at nullable, completed_at nullable, updated_at. Unique `(checklist_id, contact_id)`.

### Survey models
- `Survey`: ws, name, status draft|live|paused, `questions` PortableJSON list
  `[{id, type: "nps"|"rating"|"text"|"select", question: str, required: bool = true,
  options?: [str] (select only, 2..6)}]` (1..10 questions), `presentation: "modal"|"slideout" = "slideout"`,
  trigger/audience/schedule/frequency/priority (same shapes as tours v2), theme `{accent}`,
  thanks_message markdown = "Thanks for the feedback!", version, created_by.
- `SurveyResponse`: ws, survey_id FK CASCADE, contact_id GUID nullable indexed, `answers`
  PortableJSON `[{question_id, value: int|str}]`, completed bool, meta JSON (`{url}`), created_at.
  Index (survey_id, created_at).

### Service signatures (A1's bootstrap calls these — EXACT)
```python
async def deliverable_checklists(session, workspace_id, *, url: str, contact) -> list[dict]
  # live + trigger/audience match (reuse segments.contact_matches from A1's addition);
  # EXCLUDE when progress.dismissed_at or completed_at for the contact; anonymous → include,
  # widget hides client-side. Each dict = WidgetChecklistOut.model_dump() including
  # progress: {item_state, dismissed: bool, completed: bool} (empty for anonymous).
async def deliverable_surveys(session, workspace_id, *, url: str, contact) -> list[dict]
  # live + trigger/audience/schedule/frequency (frequency vs SurveyResponse rows: once →
  # exclude if any response; until_completed → exclude if completed response; every_time +
  # cooldown supported; default once). Anonymous → include unless frequency needs identity.
async def record_checklist_progress(session, workspace_id, checklist, contact_id, *, item_id,
    done: bool) -> ChecklistProgress   # validates item_id, sets/unsets item_state,
  # completed_at when all items done, emits CHECKLIST_EVENT {checklist_id, item_id, done,
  # contact_id, completed}, broadcasts ws topic.
async def mark_tour_completed(session, workspace_id, contact_id, tour_id) -> None
  # called by A1's record_event on tour `completed`: auto-complete matching
  # completion.type=="tour_completed" items across the contact's live checklists.
async def submit_survey_response(session, workspace_id, survey, contact_id, *, answers,
    completed: bool, meta) -> SurveyResponse  # validates question ids/types/required-if-completed,
  # nps 0..10, rating 1..5, select value ∈ options; emits SURVEY_SUBMITTED, broadcasts.
```
A1 wires `mark_tour_completed` via a try/except import INSIDE `record_event` (soft dependency —
agree on this seam; if A1b lands first/later nothing breaks).

### APIs
App (`/w/{ws}`, perms tours:read/tours:manage): `GET/POST /checklists`,
`GET/PATCH/DELETE /checklists/{id}`, `POST /checklists/{id}/publish|pause`,
`GET /checklists/{id}/stats` → `{views?, starts: n_progress_rows, completions, completion_rate,
items: [{id, title, completed_count}]}`; same CRUD surface for `/surveys` +
`GET /surveys/{id}/results` → `{responses, completed, completion_rate, by_day [{date, responses}],
nps: {score, promoters, passives, detractors} | null, ratings: {avg, distribution: {1..5}} | null,
select: [{question_id, counts: {option: n}}], text_answers: OffsetPage-style latest 50
[{value, contact_id, created_at}]}` + `GET /surveys/{id}/responses` (OffsetPage[SurveyResponseOut]).

Widget (open CORS, same light auth pattern as A1's tours: widget_key + optional X-Widget-Token):
- `POST /api/widget/checklists/{id}/progress?widget_key=` `{item_id, done}` → progress out
  (anonymous → 200 `{stored: false}` no-op, client keeps local state)
- `POST /api/widget/checklists/{id}/dismiss?widget_key=` → `{ok}`
- `POST /api/widget/surveys/{id}/responses?widget_key=` `{answers, completed}` → `{ok, thanks_message}`
  (anonymous allowed — contact_id null).

### Seeds (called from `app/dap/seed.py` — A1 owns that file; export
`async def seed_checklists_surveys(session, ctx)` from your services or a small
`app/services/dap_seed_extra.py` and A1 calls it — coordinate via this exact name)
1 live checklist "Getting started with Stept" (3 items: take welcome tour [completion
tour_completed→welcome tour], connect an inbox [url_visited */settings*], invite a teammate
[manual]); 1 live NPS+text survey "How are we doing?" (slideout, url `*/inbox*`, frequency once)
+ ~6 varied responses so the results dashboard renders.

### Tests
Model/schema validation (item/question matrices), progress lifecycle (partial→complete→
completed_at, unset), dismiss exclusion, tour_completed auto-complete seam, survey answer
validation matrix + partial submit then completed upsert-as-new-row semantics (append-only,
results count `completed` only for rate), frequency `once` exclusion after response, NPS math
(score = %promoters − %detractors, rounded int), by_day series, widget endpoints happy/anonymous/
wrong-key/cross-ws, stats endpoints, authz (viewer 403 on mutations), ws isolation everywhere.

---

## Wave A — Agent A2: Knowledge ingestion upgrades

**Owns:** `app/rag/{parsers,connectors,tasks}.py` (surgical extensions), `app/services/knowledge.py`,
`app/api/v1/knowledge.py`, `app/schemas/knowledge.py`, `tests/knowledge/*` (extend, don't break).

1. **Multi-file upload**: `POST /knowledge/sources/{id}/documents` accepts MULTIPLE `file` fields
   in one multipart request (iterate `form.getlist("file")`); response becomes
   `list[DocumentOut]` when >1 file, single `DocumentOut` (200/201 as today) when 1 — NO: keep it
   simple and typed: ALWAYS return `list[DocumentOut]` from a NEW endpoint
   `POST /knowledge/sources/{id}/documents/batch` (multipart, 1..20 files, per-file try/except —
   a bad file yields a `failed` Document with error, never aborts the batch). Existing single
   endpoint unchanged for back-compat.
2. **Crawl upgrades** (`connectors.crawl_site` + config validation in services):
   config gains `include_patterns: [glob]`, `exclude_patterns: [glob]` (fnmatch on URL path,
   exclude wins), `respect_robots: bool = true` (fetch `/robots.txt` once per sync, parse
   `User-agent: *` `Disallow:`/`Allow:` prefixes — simple longest-match, no wildcards needed;
   on fetch failure treat as allow-all), `delay_ms: int = 250` (0..2000, sleep between fetches).
   Concurrency: fetch pages with `asyncio.Semaphore(4)` + the politeness delay per fetch
   (BFS frontier semantics preserved: process depth levels in order).
3. **Sitemap incremental**: store sitemap `<lastmod>` per URL in `Document.meta["lastmod"]`;
   on re-sync skip fetching URLs whose lastmod is unchanged (still count as present for pruning).
   Missing lastmod → always fetch.
4. **Docx/PDF robustness**: pdf extraction failures per page must not kill the doc (wrap page
   `extract_text` in try/except, note `meta["failed_pages"]`); docx: also extract tables
   (rows → markdown tables, cap 100 rows/table).
5. **Authored documents**: `add_document_from_text` already exists; add `PATCH /knowledge/documents/{id}`
   accepting `{title?, content?}` for RE-EDITING authored (text/markdown, storage-key-backed) docs:
   re-saves file, re-ingests inline (hash short-circuits no-ops), 409 for non-editable docs
   (connector/url-backed). Schema `DocumentUpdate`. This is what the TipTap "write stuff" flow
   saves to.
6. Config validation + `AddSourceDialog` contract: new crawl fields validated with clamps
   (patterns ≤20 each, strings ≤200 chars).

Tests: batch upload (mixed good/bad files), robots parsing matrix (disallow/allow/missing/
malformed), include/exclude filters, delay+semaphore smoke (no timing asserts — assert fetch
order/count), sitemap lastmod skip + still-not-pruned, docx tables, pdf partial-page failure,
document PATCH re-ingest + hash-stable + 409 non-editable, existing suite stays green.

---

## Wave A — Agent A3: `packages/dom-capture` port (pure TS, no backend)

**Owns:** `packages/dom-capture/src/**` (skeleton exists: package.json/tsconfig/vitest.config
are orchestrator-owned — do not edit them).

Port from the OLD repo `/Users/ahoehne/repos/stept/packages/dom-capture/src/` (read those files
directly): `selectors.ts` (generateSelectors — ranked multi-candidate: testid 0.95, stable id
0.90, id-stem 0.62, aria 0.85→0.50 volatile, stable-prefix attr 0.76, name 0.80, text 0.70→0.45
volatile, container-anchored 0.72, minimal css path 0.60, xpath 0.30; framework-generated-id
blacklist; looksLikeVolatileText demotion), `fingerprint.ts` (FNV-1a-64 elementHash/stableHash/
structuralHash + tagPath/attrs/axName/neighborText), `resolve.ts` (resolveTarget cascade L0
primary → L1a ranked fallbacks → L1b fingerprint hashes → L1c tag+axName + container/position
disambiguation → L1d unique static attr → L2 structural score ≥0.80 with ≥0.10 margin;
scoreCandidate; targetFragility lint; roleFamilyConflict gate), `capture.ts` (buildTarget:
Target{selectors, text, aria, hints{container, position}, fingerprint, frame[], shadowPath[],
bbox}; containerHintOf/positionHintOf; interactiveAncestor; composedPath handling), `util.ts`.

Adaptations (binding):
- Replace all zod schemas from the old `@stept/schema` with plain TS interfaces exported from
  `src/types.ts` (Target, RankedSelector{kind,value,score}, SelectorKind, Fingerprint, FrameRef,
  BBox, TextInfo, AriaInfo, Hints). No runtime deps. No zod.
- Selector kinds keep the DevTools-compatible prefixes (`aria/`, `text/`, `xpath/`, `pierce/`,
  raw css) AND export `resolveSelector(kind, value, root)` used by the cascade so consumers
  never parse prefixes themselves.
- Everything must run in browser content-script AND jsdom contexts (no chrome.*, no window
  globals at module scope, accept `doc: Document` params).
- Export surface via `src/index.ts`: `generateSelectors`, `buildTarget`, `resolveTarget`,
  `resolveSelector`, `scoreCandidate`, `targetFragility`, `elementTextHint` (port the simple
  hint fn from the new extension's selector.ts for continuity), fingerprint fns, all types.
- Port the old vitest suites for these modules (adapt imports); add cases for: fingerprint
  stability across attribute-order changes, resolve healing when id changes but text+structure
  survive, pierce through open shadow roots, frame path capture. Target ≥25 tests.
- ALSO export `simpleProjection(target) -> {selector, fallback_selectors, text_hint}` — the
  bridge to the backend step fields (primary = best-scored css-ish selector the backend can
  store; fallbacks = next up-to-5 css/aria/text values; hint from text).

Verify: `pnpm --filter @stept/dom-capture test` and `pnpm --filter @stept/dom-capture typecheck`
green. This package gates B3+B4 — finish clean, report the exact export list.

---

## Wave B — Agent B1: DAP admin UI (tours v2 + analytics)

**Owns:** `frontend/src/features/tours/**`. Fill pre-registered new pages:
`pages/TourAnalyticsPage.tsx` (route `/tours/:tourId/analytics`). Others exist.
Regenerate nothing — `make types` is run by orchestrator before your wave; import from
`@/api/schema`.

- **ToursPage v2**: kind tabs (All / Flows / Banners / Announcements), "New" split-button →
  kind picker; cards keep sparkline; add duplicate action (create with `(copy)` name via
  existing POST), kind + mode badges.
- **TourEditorPage v2**: step list with per-type editor panels (type select: tooltip/modal/
  banner/action/wait; conditional fields per Step schema v2 incl. fallback-selector chips input
  (Enter-to-add, max 5), text_hint, media url + upload button (`POST /files?public=true`,
  images), advance select + delay, action kind/value/url, wait config). Settings column adds:
  Audience editor (filter rows field/op/value — same schema as segments; FIX: actually send
  `audience` on save), Schedule (two datetime inputs via calendar+time), Frequency select +
  cooldown hours, Priority number, Mode toggle guided/driven, backdrop/progress/dismissable
  switches, banner position when kind=banner. Preview panel: static in-editor mock rendering the
  selected step (tooltip bubble with title/body markdown via existing `Markdown` component) — NOT
  a live site preview; plus "Copy preview link" button → mints preview token, copies
  `https://<your-site>#stept-preview=<token>` snippet with explainer.
- **TourAnalyticsPage**: KPI tiles (starts, unique starts, completion rate, step errors),
  per-step funnel (horizontal bars: viewed + drop-off + healed count), by-day area chart
  (starts vs completions, recharts via `@/components/ui/chart`), recent events table
  (`GET /tours/{id}/events`, paged, live-append via `useRealtime('tour.event')`).
- Load the **dataviz skill guidance already applied in ReportsPage** — match its palette usage.
- Tests (~10+): step type switch renders right fields, fallback chips add/remove, audience rows
  serialize into PATCH payload, frequency/schedule serialize, funnel math rendering from mocked
  stats, preview-link mint flow, duplicate action, kind tabs filter.

## Wave B — Agent B1b: Checklists + Surveys admin UI

**Owns:** `frontend/src/features/checklists/**`, `frontend/src/features/surveys/**` (both NEW —
folders pre-stubbed with registered pages `ChecklistsPage`, `ChecklistEditorPage`, `SurveysPage`,
`SurveyEditorPage`, `SurveyResultsPage`). Routes: `/checklists`, `/checklists/:id`, `/surveys`,
`/surveys/:id`, `/surveys/:id/results`. Sidebar entries added by orchestrator.

- Checklists: list (status, completion-rate mini-stat); editor — items list (title, body
  markdown textarea, action picker w/ tour select (from `useTours`) / url, completion picker
  (manual / tour_completed + tour select / url_visited + pattern)), launcher label, position,
  theme accent, trigger/audience (same components pattern as B1 — you may duplicate small
  helpers into your feature dir; do NOT import from `features/tours` internals), publish/pause,
  stats strip from `GET /checklists/{id}/stats`.
- Surveys: list; editor — question builder (type select nps/rating/text/select, question text,
  required switch, options editor for select, reorder buttons), presentation select, thanks
  message, trigger/audience/schedule/frequency, publish/pause; **SurveyResultsPage** — KPI tiles
  (responses, completion rate), NPS gauge card (score big-number + promoters/passives/detractors
  stacked bar), rating distribution bars, select-question breakdowns, text answers list (paged),
  by-day chart. Live-append via `useRealtime('survey.submitted')`.
- Tests (~10+): item action/completion picker serialization, question builder per-type fields +
  options CRUD, NPS card math rendering, results tables from mocked payloads, publish gating by
  `tours:manage`.

## Wave B — Agent B2: TipTap editor + knowledge UI upgrades

**Owns:** `frontend/src/components/editor/**` (NEW shared component — exception to the
features-only rule, granted), `frontend/src/features/knowledge/**` (extend), widget/src/app/md.ts +
`frontend/src/features/knowledge/components/markdown.tsx` (renderer additions), and the
`AddDocumentDialog`/`AddSourceDialog`/`ArticleEditor` upgrades.

- **`RichTextEditor`** (`components/editor/RichTextEditor.tsx` + `markdown-bridge.ts` + tests):
  TipTap (StarterKit + Link + Image + Placeholder) controlled component with props
  `{value: string (markdown), onChange(md), variant: "full"|"compact", placeholder?}`.
  `markdown-bridge.ts`: `markdownToTiptapDoc(md)` and `tiptapDocToMarkdown(doc)` for the
  CONSTRAINED schema: h1-h4, bold, italic, inline code, code fence, ul/ol (nested ≤3), blockquote,
  link, image, hr, paragraphs. If the installed TipTap version ships an official markdown
  extension, use it and delete the hand bridge for that direction; otherwise the bridge is the
  source of truth — exhaustive round-trip tests either way (md → doc → md stable on the
  constrained set). Toolbar: full = headings/bold/italic/code/code-block/lists/quote/link/image
  (image → prompt URL or upload via `POST /files` for articles [not public] — use returned url);
  compact = bold/italic/code/link only. Keyboard shortcuts + bubble menu for links. Dark mode.
- **Wire in**: `ArticleEditor` (replace Textarea; keep a "Markdown" tab showing raw md in a
  Textarea, two-way synced on tab switch), knowledge "Paste text" flow → "Write document"
  (title + RichTextEditor full), NEW authored-doc editing: in `SourceDetailPage`, text/markdown
  storage-backed documents get an Edit button → dialog/page with RichTextEditor loading current
  content (`GET /knowledge/documents/{id}` returns chunks not raw content — use new
  `PATCH /knowledge/documents/{id}` + fetch raw via… A2 exposes raw content on
  `DocumentDetailOut.content` for editable docs — coordinate: A2 adds `content: str | null`
  (raw text, editable docs only, ≤200KB) to `DocumentDetailOut`).
- **Uploads UX**: `AddDocumentDialog` → FileDrop-style drag&drop, `multiple`, `accept`
  ".pdf,.docx,.html,.md,.txt,.csv", per-file progress/status list, uses the new batch endpoint.
  `AddSourceDialog` crawl tab gains include/exclude patterns (chips), respect-robots switch,
  delay slider.
- **Renderer additions**: images (`![alt](url)`) + tables (GFM pipes, no alignment needed) in
  BOTH `markdown.tsx` (React) and `widget/src/app/md.ts` (HTML string; keep `safeUrl` guard,
  img restricted to http/https) + tests. (Tour step bodies render via widget md — B3 consumes.)
- Tests (~12+): bridge round-trips (every node type), editor onChange emits markdown, toolbar
  toggles, article editor tab sync, batch upload flow states, renderer image/table cases incl.
  javascript: URL rejection.

## Wave B — Agent B3: Widget player v2 + experiences

**Owns:** everything under `widget/` (extend existing files; keep all current tests green).

- **Experiences bootstrap**: replace `fetchTours` with `fetchExperiences(apiBase, widgetKey, url,
  token)` → `GET /api/widget/experiences` `{tours, checklists, surveys}`. Keep re-check on SPA
  url change (tours+surveys re-evaluate; checklist set is stable per page-load unless url
  filter changes it). Priority: active tour > survey > checklist auto-open; never two overlays
  at once; checklist launcher pill coexists.
- **Selector engine** (`widget/src/dom-target.ts` NEW, unit-tested): thin adapter over
  `@stept/dom-capture` (workspace dep, import from `@stept/dom-capture`): `resolveStepTarget(step,
  doc)` — if `step.target` present use `resolveTarget` (full cascade incl. fingerprints/shadow/
  frames*), else build a minimal Target from `{selector, fallback_selectors, text_hint}` and run
  the same cascade; returns `{el, healed, via} | null`. `waitForTarget(step, doc, timeout_ms)` —
  MutationObserver + 300ms poll fallback, resolves early, null on timeout. (*cross-frame steps:
  v1 the widget only resolves in the top document — if `target.frame` is non-empty, treat as
  not-found with `meta.reason="in_iframe"`; the extension handles frames.)
- **Player v2** (`tour-player.ts` extension): step `type` support — tooltip (as today + media
  img/video top, markdown body via `md.ts`, advance modes: button (default), element_click
  (click target advances; also driven-mode auto), input (advance on target input+blur or Enter),
  delay (auto-advance)), modal (centered card, no spotlight), banner (top/bottom full-width bar,
  CTA buttons), action (DRIVEN: highlight pulse 600ms → `el.click()` or fill (set value +
  dispatch input/change) or navigate (location.assign) → advance; on `settings.mode=="guided"`
  render as tooltip instructing the user, advance on element_click), wait (spinner-less hidden
  step: waitFor element/url up to timeout → advance; timeout → step_error+skip). Respect
  `settings` (backdrop off → no spotlight dim; show_progress → "2 of 5" + progress bar;
  dismissable false → no ×/Esc). `step_error` event with `meta.reason`; healed steps send
  `meta.healed=true`. **Progress persistence**: sessionStorage `stept:tour-progress:{widgetKey}`
  `{tourId, stepIndex, startedAt}` → resume after reload WITHOUT re-emitting `started`.
  **A11y**: Esc dismiss (if dismissable), ArrowRight/ArrowLeft, focus tip on step show, restore
  focus on end, `aria-modal` + labelledby. **Fixes**: pushState patch idempotent (store original
  fns on window symbol; shutdown restores), reposition via ResizeObserver on target +
  rAF-throttled scroll (keep listeners), banner kind never blocks page interaction.
- **Checklist UI** (in-host-DOM like tours, `widget/src/checklist-widget.ts` NEW): launcher pill
  (bottom corner opposite the messenger launcher or same side stacked; position from theme),
  panel with items (checkbox state, title, md body collapsible, action button "Start" →
  start_tour via player / open_url / open_messenger opens the iframe panel), progress bar,
  dismiss (confirm), auto_open_once via localStorage flag. Progress: identified → POST progress;
  anonymous → localStorage `stept:checklist:{widgetKey}:{id}`. Auto-completion: url_visited items
  checked on every url change (fnmatch-lite glob); tour_completed items optimistically checked
  when the player completes that tour id (server does it authoritatively for identified).
- **Survey UI** (`widget/src/survey-widget.ts` NEW): slideout card (bottom corner) or modal per
  `presentation`; question flow one-at-a-time (NPS 0-10 buttons, rating 5 stars, text textarea,
  select options list), progress dots, submit partial-on-dismiss (completed=false), thanks
  message md, seen-set + frequency semantics like tours (`every_time` bypasses local seen-set).
- **Glob fix**: `globMatch` case-insensitive. **startTour fix**: use `GET /api/widget/tours/{id}`.
  **Preview mode**: on boot, if `location.hash` contains `stept-preview=<token>` → fetch that
  tour with preview_token and start immediately (ignore seen-set), badge "Preview" on the tip.
- Tests (~15+): dom-target resolution matrix (fallbacks, text-hint, healed flag, queryDeep,
  waitFor timeout), per-step-type advance semantics (jsdom: element_click, delay w/ fake timers,
  input), driven action execution (click/fill dispatch), progress persistence resume, checklist
  local progress + url_visited auto-check, survey answer collection + partial submit, preview
  hash parsing, glob case-insensitivity. Keep existing 47 green.

## Wave B — Agent B4: Chrome extension v2 (the ported full extension)

**Owns:** everything under `extension/` EXCEPT `package.json` (orchestrator-owned, deps final:
wxt 0.20 + @wxt-dev/module-react + react 19 + react-dom + lucide-react + @stept/dom-capture).
You own `wxt.config.ts` (expand the placeholder). This is a PORT of the old extension at
`/Users/ahoehne/repos/stept/extension` — READ ITS SOURCE as you work; the capability map is in
`docs/research/old-extension-map.md`. Port aggressively (the old code is ours), adapt the API
layer to the new backend (A1's `/api/widget/dap/*`). Old sidepanel components are React — they
port near-verbatim. Delete the obsolete Vite-era files (vite.config.ts, vite.content.config.ts,
index.html, src/popup.tsx, src/App.tsx, src/popup.css, src/chrome.d.ts) once replaced.

- **WXT structure** (mirrors the old repo): `src/entrypoints/background.ts` (service worker),
  `src/entrypoints/recorder.content.ts` (all_frames, document_start), `src/entrypoints/
  guide.content.ts` (player overlay), `src/entrypoints/sidepanel/` (React app). Manifest via
  wxt.config.ts: permissions `[storage, tabs, scripting, sidePanel, activeTab, downloads,
  webNavigation, alarms, debugger]`, host_permissions `["<all_urls>"]`, side_panel default_path,
  action + badge, command `Ctrl+Shift+S`/`Cmd+Shift+S` toggles recording, icons (generate simple
  16/48/128 PNGs — solid accent square with an "S"; a tiny script or hand-encoded PNGs are fine).
  Extend `tsconfig.json` to WXT convention (`extends: ./.wxt/tsconfig.json`) so auto-import
  globals typecheck; run `pnpm --filter @stept/extension exec wxt prepare` first.
- **Login with the real auth system** (replaces old PKCE+pairing): sidepanel Login screen
  (apiBase [default http://localhost:8600] + email + password) → background fetch
  `POST /api/v1/auth/login` → workspace picker from the login response's user/memberships (or
  `GET /api/v1/me`) → `POST /api/v1/w/{ws}/tours/extension-token` → store `{extensionToken,
  workspaceId, workspaceName, apiBase, userName}` in `chrome.storage.local`; DISCARD the access
  token + password immediately. Port the single-flight/silent-retry patterns from old
  `stept-api.ts`; on 401 → sign-out state with re-login prompt. Manual token paste stays as an
  "Advanced" fallback (accepts recorder or extension tokens). `POST /api/widget/dap/auth/check`
  validates on startup.
- **Recorder v2**: port `recorder.content.ts` event capture (pointer/click/dblclick/context,
  debounced input w/ secret redaction, change→select/check/upload, keydown combos, throttled
  scroll, hover-reveal detection, SPA nav via popstate+href-poll; background adds
  webNavigation/downloads/tabs observers), port `capture-hold.ts` pre-capture screenshot pairing
  (captureVisibleTab jpeg q92 at pointerdown, per-tab throttled) + `secret-redaction.ts`.
  Screenshots upload EAGERLY to `POST /api/widget/dap/screenshots` → `{key}` kept on the raw
  event. Port the compiler subset (`orderEvents`, typing coalescence, dblclick collapse, Enter
  fold, hover dedupe, synthetic-submit drop, titling/slugify from old
  `packages/compiler/src/index.ts`) into `extension/src/compiler/` emitting the NEW TourStep v2
  shape: clicks → `tooltip` steps w/ `advance: {on:"element_click"}` + full `target` (via
  @stept/dom-capture `buildTarget` at capture time) + simpleProjection fields; typed input →
  `action {kind:"fill"}` steps (value masked if secret); navigations → `wait {for:"url"}` steps;
  placement derived from bbox vs viewport quadrant.
- **Sidepanel manager** (port `Sidepanel.tsx`, `HomePanel`, `StepCards`, `SettingsDrawer`,
  `Login`, `GuidePanel`, `SaveSheet`, `SavedBanner`, `ProjectChooser`→WorkspaceChooser): record
  start/stop/pause with live step cards (screenshot thumb + click marker, inline rename, delete,
  reorder, edit body), save sheet (name + url_pattern suggestion) → `POST /api/widget/dap/tours`
  (draft) with steps incl. screenshot_key + target → "Open in Stept" link; tour list tab
  (`GET /api/widget/dap/tours`) with pull-to-edit (`PUT .../steps`, base_version conflict → 409
  friendly reload) and Play/Drive buttons; badge shows step count while recording. Session state
  in `chrome.storage.session` (survives SW teardown; keepalive alarm port).
- **Guide mode (viewing)**: port `guide-core.ts` + `guide.content.ts` WHOLESALE (spotlight veil
  + SVG mask hole, pulsing ring, tooltip card w/ progress, rAF position tracking, 400ms/10s
  resolve loop w/ notfound→Skip, action-completion listeners per step type, nav-race handling).
  Adapt: resolution goes through `@stept/dom-capture.resolveTarget`; step payloads are TourStep
  v2; `navigate`/`wait` steps handled by background (`chrome.tabs.update`, url matching).
- **Drive mode (browser driving)**: LOCAL runner (no server WS): port the `locate()` loop from
  old `packages/replayer-core/src/runner.ts` (strong/weak confidence banking, deadline, healing
  cascade — L3 LLM healing OMITTED, note it) into `extension/src/driver/`, and the CDP executor
  subset from `executor-extension.ts` (`chrome.debugger` attach, Input.dispatchMouseEvent
  trusted clicks, Input.insertText typing, key events, scroll, `dom-settle.ts` quiet-window)
  to EXECUTE tour steps on the current tab: action steps performed for real, tooltip steps
  shown briefly (auto-advance after `settings`-scaled delay), wait steps honored. Controls in
  sidepanel GuidePanel: play/pause/stop, speed 0.5×/1×/2×, per-step status list, error surface
  on step failure (offer Skip/Abort). Fall back to synthetic events (el.click()) when debugger
  attach is refused. This is the "do it for me" browser-driving mode.
- **Selector picker**: standalone picker mode (hover highlight + click captures Target +
  simpleProjection; shown in a copyable panel) for re-targeting steps and for the dashboard's
  "copy selector" flow.
- Tests (vitest, jsdom, ~15+): compiler passes (coalescence/collapse/fold/titles), payload
  build, secret redaction matrix, guide-core pure fns (port old tests), url-pattern suggestion;
  pure modules only (no chrome.*). README rewrite: load-unpacked from `extension/dist/chrome-mv3`,
  login flow, record→save→edit→publish walkthrough, drive-mode caveats (debugger permission
  banner). `pnpm --filter @stept/extension build` + `test` green.

## Wave C — e2e + integration (orchestrator + 1 agent)

New Playwright journeys (`e2e/tests/dap-journey.spec.ts`, `knowledge-journey.spec.ts`):
(1) create flow tour w/ modal+tooltip steps in dashboard → publish → demo page: widget plays it,
events land in analytics page; (2) checklist: seed checklist visible in demo page launcher, check
an item, dashboard stats reflect it; (3) survey: NPS slideout on demo page, submit, results page
shows score; (4) upload PDF via batch dialog → search playground finds it; (5) write article in
TipTap editor → publish → portal renders → widget help-center finds it. Extension e2e stays
unit-level (Playwright extension harness is out of scope this wave — note in docs).

---

## Orchestrator-owned (I do these — agents DO NOT)

- Deps: frontend adds tiptap packages (+ `@tiptap/pm`); everything else exists. pnpm install.
- Registries: `app/api/v1/__init__.py` (checklists, surveys routers), widget router registry
  (`app/api/widget/__init__.py` or main.py include — dap, checklists, surveys, media),
  `app/models/__init__.py` (checklist, survey), `app/core/events.py` (2 new names),
  `frontend/src/router.tsx` (5 new routes + analytics route), sidebar entries, page stubs,
  feature folder stubs, `tests/{checklists,surveys,dap}/__init__.py`.
- `make types` between waves; alembic revision `0003_dap2` after Wave A; PG validation
  up/down/up; seeds orchestration check; final `make verify`; PLAN.md/README updates; merge.
