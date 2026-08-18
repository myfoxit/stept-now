"""The deep authoring contract, served on demand by ``get_authoring_guide``.

Division of labor with :data:`app.mcp.server.SERVER_INSTRUCTIONS`: the handshake
instructions are a compact ROUTING MAP (which tool for which intent) and are
paid for on every connection; this guide is the contract for content that
actually *renders*, and is paid for only when someone is authoring.

Every rule here mirrors a validator in ``app/schemas/{tours,checklists,surveys}.py``
or a gate in ``app/services/*.py``. When those change, change these — a guide
that lies is worse than no guide, because the model believes it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuideSection:
    id: str
    title: str
    body: str


#: Returned when ``get_authoring_guide`` is called with no arguments, alongside
#: the table of contents. Everything an author needs before their first write.
CORE_SECTION_IDS = ("lifecycle", "publish-requirements")


GUIDE_SECTIONS: tuple[GuideSection, ...] = (
    GuideSection(
        id="lifecycle",
        title="Lifecycle (read this first)",
        body="""\
Every DAP experience — tour, checklist, survey — has the same three states:

    draft  →  live  →  paused
             (publish)  (pause)   pause → publish returns it to live

- `create_*` always produces a **draft**. Drafts are never delivered to anyone.
- `publish_*` sets status to `live`. Only live experiences reach visitors.
- `pause_*` sets status to `paused`. It stops being delivered; its analytics stay.
- Editing is allowed in **any** state. There is no immutable-version rule in Stept:
  an edit to a live experience takes effect on the next widget bootstrap.
- `version` bumps automatically when *content* changes (steps / items / questions),
  not when targeting or theme changes. The widget uses the bump to invalidate a
  visitor who is mid-way through, so do not try to set it yourself.

Order of operations that works:

  1. `get_authoring_guide` (you are here) → fetch the sections for your type
  2. `get_experience_schema` for the exact field shapes
  3. `create_tour` / `create_checklist` / `create_survey` (lands as draft)
  4. `validate_experience` — catches "publishes green, never renders"
  5. `publish_tour` / `publish_checklist` / `publish_survey`
  6. `diagnose_experience` if it still is not showing up

Do not skip step 4. The schema validators catch malformed input; `validate_experience`
catches *well-formed content that cannot reach anyone* — a live tour with a manual
trigger and no caller, an audience filter that matches zero contacts, a checklist
item pointing at a deleted tour.""",
    ),
    GuideSection(
        id="tour-steps",
        title="Tour steps",
        body="""\
A tour's `steps` is an ordered list. Six step types, and the per-type rules are
enforced server-side — a step that violates them is rejected, not silently fixed:

| type      | anchors to an element | extra config required |
|-----------|-----------------------|------------------------|
| `tooltip` | **yes** — `selector`  | —                      |
| `hotspot` | **yes** — `selector`  | —                      |
| `action`  | **yes** — `selector`  | `action` (see below)   |
| `modal`   | no (centered)         | —                      |
| `banner`  | no (docked bar)       | —                      |
| `wait`    | no (invisible)        | `wait` (see below)     |

Common fields: `title` (≤200), `body` (markdown — see the `markdown` section),
`placement` (`auto|top|bottom|left|right|center`), `media` ({type: image|video, url}),
`cta` / `secondary_cta` ({label ≤60, url}).

**`advance`** — how the visitor moves on. `{"on": "button"}` (default),
`"element_click"` (they click the anchored element), `"input"` (they type into it),
or `"delay"` which **requires** `delay_ms` (100…600000).

**`action` steps** do it *for* the visitor. `{"kind": "click"}`,
`{"kind": "fill", "value": "…"}` (value required), `{"kind": "navigate", "url": "…"}`
(url required). These only run when the tour's `settings.mode` is `driven`.

**`wait` steps** pause until the page catches up. `{"for": "element", "selector": "…"}`
(falls back to the step's own `selector`) or `{"for": "url", "url_pattern": "…"}`
(url_pattern required). `timeout_ms` defaults to 10000, max 120000.

**CTA urls** must be `http`, `https`, `mailto` or `tel`. A `javascript:` or `data:`
URL is rejected — the player puts this in an href on the customer's own page, so
anything else is stored XSS.""",
    ),
    GuideSection(
        id="targets",
        title="Targets: making a step find its element",
        body="""\
This is where authored tours break. Read it before writing a `selector`.

- `selector` (≤500 chars) is the primary CSS selector. Required for
  `tooltip` / `hotspot` / `action`.
- `fallback_selectors` (≤5) are tried in order when the primary misses. Spend them:
  a tour with fallbacks self-heals through a re-render, one without goes red.
- `target` is an opaque descriptor produced by `@stept/dom-capture` (≤8KB). You cannot
  author it by hand and you should not try — it is what makes healing work, and it is
  stamped for you when a step is recorded.
- `text_hint` (≤80, auto-trimmed) is the human label of the element. It is the last
  resort the healer uses. Always set it; it costs nothing and rescues renamed classes.
- **`url` is per step**, not per tour. It is the page the step lives on. The player
  navigates there before resolving the anchor, which is the only reason a recorded
  multi-page tour replays. A multi-page tour whose steps have no `url` will emit
  `step_blocked` telemetry and show the visitor a "can't find it" card.

**Prefer recording over guessing.** Stept can drive the user's real Chrome:
`browser_open` the app, `browser_record_start`, walk the flow with `browser_act`,
then `browser_record_stop` — you get real selectors, real `target` descriptors, real
per-step `url`s, and a draft tour. Hand-written selectors are a fallback for when no
browser is connected, not the default path.

Prefer stable attributes in this order: `[data-testid]` → `[data-*]` → `#id` →
semantic role+text → class. Never author a selector containing a hashed build class
(`.css-1x2y3z`) — it will not survive the next deploy.""",
    ),
    GuideSection(
        id="targeting",
        title="Targeting: trigger, audience, schedule, frequency, priority",
        body="""\
The same five-field vocabulary covers tours, checklists and surveys.

**`trigger`** — when it fires.
- `{"type": "url_match", "url_pattern": "*/settings*"}` — auto-delivered on matching
  pages. Glob, case-insensitive, matched against the full URL.
- `{"type": "manual"}` — **never auto-delivered.** It only plays when something asks
  for it by id (`stept('startTour', id)`, a checklist item's `start_tour` action, the
  in-app agent's `show_guide`, or `browser_run_tour`). Choosing `manual` and then
  wondering why nothing shows is the single most common authoring mistake.

**`audience`** — who sees it. `{"type": "all"}` or
`{"type": "filters", "filters": [{field, op, value}, …]}`, ANDed.
`field` is one of `email`, `name`, `external_id`, `last_seen_at`, `created_at`,
`verified`, or `attributes.<your_key>`. `op` is one of `eq`, `neq`, `contains`,
`starts_with`, `exists`, `not_exists`, `gt`, `lt`.
Note that audience applies to manual starts too — a tour aimed at enterprise trials
does not play for everyone else just because something asked for it by id.

**`schedule`** — `{"start_at": iso, "end_at": iso}`, UTC, both optional. Empty = always.

**`frequency`** — per-contact re-delivery. `once` (never again after it starts),
`until_completed`, `until_dismissed` (the tour default), `every_time` (+ optional
`cooldown_hours`). Frequency governs *unsolicited* delivery only — it deliberately
does not apply when something asks for the tour by id.

**`priority`** — higher first. The widget bootstrap delivers at most **5** matching
experiences per page; below that cutoff nothing renders, so a low-priority tour on a
busy page is effectively off.""",
    ),
    GuideSection(
        id="checklists",
        title="Checklists",
        body="""\
A checklist is a persistent launcher plus up to **20** items.

`launcher`: `{"label": "Getting started", "auto_open_once": true}` — the pill in the
corner. `theme.position` is `bottom-right` or `bottom-left`.

Each item: `title` (1…200, required), `body` (markdown), plus two tagged unions.

**`action`** — what the item's CTA does:
- `{"type": "start_tour", "tour_id": "…"}` — tour_id required, and it must exist
- `{"type": "open_url", "url": "…"}` — url required
- `{"type": "open_messenger"}` — opens the support thread
- `{"type": "none"}` (default)

**`completion`** — how it gets checked off:
- `{"type": "manual"}` (default) — the visitor ticks it
- `{"type": "tour_completed", "tour_id": "…"}` — tour_id required
- `{"type": "url_visited", "url_pattern": "…"}` — url_pattern required

The pairing that works: `action: start_tour` + `completion: tour_completed` on the
same `tour_id`, so doing the thing ticks the box. An item whose action starts tour A
but whose completion watches tour B is legal and almost always a bug — `validate_experience`
flags it.""",
    ),
    GuideSection(
        id="surveys",
        title="Surveys",
        body="""\
Up to **10** questions, four types:

- `nps` — 0…10, the standard promoter scale
- `rating` — 1…5
- `text` — free text
- `select` — needs `options`: between **2 and 6**, non-blank, unique

Every question: `question` (1…300, required), `required` (default true), `id` (assigned
for you if omitted).

`presentation` is `modal` or `slideout` (default). `thanks_message` (≤2000) shows after
submission. Targeting is the same five-field vocabulary as tours, except `frequency`
defaults to `once` — which is almost always what you want for a survey.

Results: `nps`, `rating` and `select` distributions are computed from **completed**
responses only (a half-filled card is not a verdict); `text` answers include partials
so no written feedback is lost. Read them with `get_survey_results`.""",
    ),
    GuideSection(
        id="banners-announcements",
        title="Banners and announcements",
        body="""\
These are tours with a different `kind`, not separate types:

- `kind: "banner"` — a single-step docked bar. `theme.position` is `top` or `bottom`;
  `theme.banner` controls `layout` (`overlay` floats above the page, `inline` pushes
  the document so it never covers the host app's own navigation), `full_width`,
  `max_width` (240…2000, only when full_width is off), `align`, `background`,
  `text_color`, `icon` (one emoji), `rounded`, and `dismiss` (`dismiss` or
  `never_again`, which suppresses it for that contact regardless of `frequency`).
- `kind: "announcement"` — a single-step centered modal.

Both should have exactly **one** step. Colors must be CSS color literals — they reach
the page as inline custom properties, so anything that could close the declaration and
inject rules is rejected.

**Autostart nuance that surprises people:** when the workspace agent's
`tour_autostart_policy` is `ask` (the default), flow tours are gated behind a
confirmation pill — but banners and announcements are **exempt** and appear directly.
If you want something to appear without the visitor agreeing to it first, that is what
banner/announcement kind is for.""",
    ),
    GuideSection(
        id="markdown",
        title="Text (markdown subset)",
        body="""\
Step `body`, checklist item `body` and article content render through the widget's
markdown renderer. Supported: paragraphs, `**bold**`, `*italic*`, `` `code` ``, links,
bullet and numbered lists, headings, images.

Not supported, and silently dropped: raw HTML, tables, footnotes, task lists.

Images must use absolute URLs or root-relative paths (`/media/…`). A relative path
(`media/…`) resolves against the customer's page, not against Stept, and disappears.

Keep step bodies to roughly two short sentences. A tooltip is not a document — if the
text does not fit in a tooltip on a laptop, it belongs in a modal step or a help-center
article you link to with a CTA.""",
    ),
    GuideSection(
        id="sdk",
        title="Making it appear (the embed)",
        body="""\
Nothing you author reaches anyone unless the widget is on the page. The host app needs
the loader snippet (Settings → Widget in the dashboard gives you one with the real key):

    <script>
      window.Stept = window.Stept || function(){ (Stept.q = Stept.q || []).push(arguments) };
    </script>
    <script async src="https://<host>/widget/loader.js" data-stept-key="wk_…"></script>

Then, to target anyone by anything other than anonymous browsing:

    Stept('identify', { external_id: 'user_123', email: '…', attributes: { plan: 'pro' } });

`audience` filters on `external_id`, `email` or `attributes.*` match **nothing** for an
anonymous visitor. If a tour targets `attributes.plan` and the host never calls
`identify`, it will validate green, publish green, and never show — `diagnose_contact`
is how you catch that.

To start something explicitly: `Stept('startTour', '<tour_id>')`.""",
    ),
    GuideSection(
        id="publish-requirements",
        title="What each type needs before publishing is worth doing",
        body="""\
`validate_experience` enforces these. It returns `{ok, errors, warnings}` — errors mean
publishing produces something that cannot render or cannot be reached; warnings mean it
will render but probably not to whom you intended.

**Tour (kind=flow)** — at least one step; every `tooltip`/`hotspot`/`action` step has a
non-empty `selector`; every `action` step has an `action`; every `wait` step has a `wait`;
if any step has a `url`, all should (a partial set means the player cannot navigate).
Warning if trigger is `manual` and no checklist item or other content references it.

**Tour (kind=banner|announcement)** — exactly one step, with body text.

**Checklist** — at least one item; every `start_tour` action and `tour_completed`
completion points at a tour that exists and is (or can be) live; the launcher has a label.

**Survey** — at least one question; every `select` has 2…6 unique non-blank options.

**All types** — if `audience.type` is `filters`, the filters must match at least one
known contact, else it is a warning: correct content, zero audience. If `schedule.end_at`
is in the past it is an error — publishing something already expired.""",
    ),
    GuideSection(
        id="diagnosis",
        title="When it does not show up",
        body="""\
Do not re-target blindly. Two tools answer two different questions:

**`diagnose_experience(id)`** — "why isn't this showing?" Evaluates the gates the widget
itself applies, in the order it applies them: `status` (is it live?), `trigger` (does the
pattern match the URL you are asking about?), `schedule` (are we inside the window?),
`audience` (does this contact match?), `frequency` (has this contact already seen it?),
`content` (is there anything to render?). Each gate comes back `pass` / `fail` / `unknown`
with a reason. `unknown` means the gate depends on a runtime fact not supplied — pass
`url` and `contact_id` to turn unknowns into verdicts.

**`diagnose_contact(contact_id, url)`** — "what would this visitor see on this page right
now?" Sorts every live experience into `showing` / `blocked`, with the delivery cap and
priority ordering already applied. Start here for "customer X says onboarding is broken",
then deep-dive one experience with `diagnose_experience`.

A gate is a judgment ("does this block?"), a condition is a fact ("is this satisfied?").
An audience filter that is *satisfied* is reported `matched` even when the gate it feeds
ends up failing for another reason — read the gate for the verdict, the conditions for
the cause.

If everything passes and it still does not render, the problem is on the page, not in the
config: use `browser_open` + `browser_snapshot` to look at the real DOM, and check
`tours_health` for `step_error` / `step_blocked` telemetry from real visitors.""",
    ),
)

_SECTIONS_BY_ID = {section.id: section for section in GUIDE_SECTIONS}

#: Table of contents, returned on every `get_authoring_guide` call.
TABLE_OF_CONTENTS = [{"section": s.id, "title": s.title} for s in GUIDE_SECTIONS]

SECTION_IDS = tuple(_SECTIONS_BY_ID)


def render_sections(section_ids: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    """Render the requested sections, preserving guide order.

    Returns ``(sections, unknown_ids)`` — an unknown id is reported rather than
    raising, so one typo in an array of five does not lose the other four.
    """
    wanted = set(section_ids)
    unknown = sorted(wanted - set(_SECTIONS_BY_ID))
    rendered = [
        {"section": s.id, "title": s.title, "body": s.body}
        for s in GUIDE_SECTIONS
        if s.id in wanted
    ]
    return rendered, unknown
