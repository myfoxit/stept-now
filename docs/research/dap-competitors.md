# DAP competitor feature research (July 2026) — build reference for Wave 7

Scope: Pendo, WalkMe, Appcues, Userflow, Userpilot, Chameleon, Whatfix, Product Fruits,
Usetiful, Intercom, Candu + OSS (Usertour, Flows, driver.js, Shepherd, intro.js, Onborda,
NextStepjs). Full citations in the research transcript; key engineering takeaways below.

## Table stakes (every serious DAP)

1. Multi-step flows mixing tooltip + modal steps, cross-page, SPA-safe
2. Per-step advance rules: button / click target element / input into field (Intercom's exact set)
3. Hotspots/beacons + persistent tooltips (standalone, not just tour steps)
4. Banners + modals as standalone announcements
5. Checklists: progress, task→flow links, event-based auto-completion, celebration state
6. Surveys: NPS + rating + multi-choice + free text, response dashboards
7. Resource center in the widget: guide list + search + announcements
8. Extension-based no-code builder pointing at the live app
9. Targeting: attributes + events + segments + URL rules + device
10. Frequency: show-once vs recurring, dismissal persistence, per-user state server-side
11. Auto-start priority resolution (Userflow: priority → unseen-first → least-recently-shown → oldest)
12. Themes (tokens) + dark mode
13. Per-step funnel analytics (views/drop-off/completion) + survey dashboards
14. Preview/test mode + condition debugger (Appcues shows per-condition pass/fail with values)
15. JS API (identify/track/start/stop) + webhooks
16. Selector capture with fallback strategy and defined not-found behavior
    (Appcues marks the step "Content omitted")

## Reference behaviors worth copying exactly

- **Pendo activation modes**: Automatic, Badge (injected icon), Target Element (native element
  click launches), Confirmation (intercepts a click, asks confirm). Guide **throttling**:
  minimum time gap between automatic guides + ordering so they never stack.
- **WalkMe Smart Walk-Thrus**: flowchart authoring with Split Steps (rule → true/false),
  Wait-For steps (Rule Engine), Auto-Steps (click/type/hover for the user; one-click convert
  guided→auto), Auto-Play; ActionBot actions run walk-thrus + external APIs. SmartTips do
  field-level input validation (AND/OR rules, evaluated live, nothing stored).
- **Userflow conditions**: attributes, events, URL, element present/clicked, text-input value,
  current time; checklist tasks auto-complete on tracked events; surveys with answer-based
  branching; no-code click/visibility event trackers usable in targeting.
- **Chameleon rate limits**: global cap of experiences per user per period across the pooled
  set, per-experience exemptions. A/B via persistent random 0–100 Testing ID property.
- **Appcues frequency**: deliberately simple "show once" vs "show every time" + triggers.
- **Whatfix Auto Testing**: proactively crawls content to find broken selectors before users
  do; draft→production, version history, rollback, environments.
- **Intercom tours**: 3 step types (post, pointer, video pointer ≤40MB), advance Next/click/
  type, multi-page only via click-progression, shareable tour URLs. No branching. Web only.

## OSS landscape

- **Usertour** (AGPL, self-host): the only real OSS DAP platform — WYSIWYG builder, tours/
  checklists/surveys/NPS/launchers/banners, themes, segmentation, environments, analytics.
  Lacks: branching, resource-center depth, do-it-for-me, A/B, localization, self-healing,
  and any support/inbox integration.
- **Flows** (flows.sh): headless bring-your-own-components; platform proprietary.
- driver.js / Shepherd (AGPL) / intro.js (AGPL) / Onborda / NextStepjs: renderers only — no
  state, targeting, analytics, builder.
- **Gap we exploit**: no OSS tool has do-it-for-me automation, self-healing selectors,
  breakage telemetry, or an integrated inbox/AI-agent loop.

## Differentiators chosen for stept (ranked, feasible now)

1. Resource center + checklists inside the existing messenger widget
2. Do-it-for-me auto-steps (extension CDP drive + widget driven mode)
3. Self-healing selectors + per-step breakage telemetry (healed/step_error events → analytics)
4. Conversation-triggered guidance (agent sends a tour link) — v1: deep-link support
5. Survey→conversation fusion (NPS detractor → open conversation) — roadmap
6. Cmd-K HelpBar over articles+tours — partially exists (cmd-k + RAG search)
7. Global frequency governance — roadmap (per-experience frequency ships now)
8. Self-hosted/data ownership — inherent
9. Flow branching + wait steps — wait steps ship now; branching roadmap
10. A/B + goals — roadmap

## Roadmap parking lot (explicitly deferred from Wave 7)

Goals/A-B testing, localization, global rate limits, no-code event trackers, inline embeds
(Candu-style), announcement feed tab, condition debugger UI, mobile SDKs, branching splits,
remote agentic driving (WS), NPS recurring sampling programs.
