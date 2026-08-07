# Chatwoot → Stept: prioritized gap backlog

> Source-level diff, 2026-08-07.
> Chatwoot: fresh clone @ `0f3bb640` (2026-08-07), including `enterprise/` (their paid tier — read
> because it defines the competitive ceiling, not because it's free).
> Stept: `master` after W8 (1731 tests).
> Supersedes the gap tables in `docs/COMPETITIVE.md`, which were written pre-W6 and whose 🚧 rows all shipped.
>
> Excluded by request: billing/Stripe, plan limits & cloud feature flags, super-admin console,
> internal marketing/threat analytics, account-deletion flows.
>
> Effort: **S** ≤2 dev-days · **M** 3–8 · **L** 2–4 weeks · **XL** >1 month.

---

## 0. The one-paragraph read

Stept is **ahead of Chatwoot on AI depth, on the entire DAP category, and on what ships in the free
tier** — SLA, custom roles, and audit log are all `enterprise/` (paid) in Chatwoot and MIT-core in
Stept. Where Chatwoot is ahead is almost entirely **the unglamorous operational middle**: business
hours, mentions, saved views, bulk triage, contact merge/import, real reports, notification
preferences, email done properly, and i18n. None of it is hard; all of it is what a support team
hits in week one. There is exactly **one** place where Chatwoot has a strategically better *idea*
than Stept — the FAQ-mining loop that turns resolved conversations into curated, human-approved
answers (§3.1). That one is worth copying carefully.

The failure mode to avoid: spending the next three waves on channel breadth and enterprise SSO while
the daily-driver gaps stay open. Ship §1 first — it's mostly S/M work and it's what makes Stept
survivable as someone's actual inbox.

---

## 1. P0 — daily-driver blockers

A support team adopting Stept hits every one of these in the first week and cannot work around them.

### 1.1 Business hours / working-hours model · **M**
**Chatwoot:** `working_hours` (per inbox × day_of_week: open/close hour+minutes, `open_all_day`,
`closed_all_day`; auto-seeded Mon–Fri 9–17), inbox-level `working_hours_enabled` / `timezone` /
`out_of_office_message`, and `enterprise/app/services/sla/business_hours_service.rb`.

**Stept:** nothing. `grep -ri business_hour|working_hour backend/app` → zero hits.

**Why P0:** Stept's SLA policies are **wall-clock only**. For any team that isn't 24/7, every
weekend and every night counts against FRT, so the SLA report is not just imprecise — it's
unusable, and the breach notifications are noise that teams will mute. Business hours is also the
gate for OOO auto-replies, business-hours-only campaigns, and AI `response_window`.

**Build:** `working_hours` table + `app/core/business_hours.py` (elapsed-business-seconds between two
instants, given a schedule + tz) → wire into `services/slas.py` behind a per-policy
`only_during_business_hours` flag; OOO message on the widget boot payload; `trigger_only_during_business_hours`
on campaigns. Test the DST boundary and the "opened Friday 18:00, replied Monday 09:30" case.

---

### 1.2 @mentions + conversation participants (watchers) · **M**
**Chatwoot:** `mentions` (user × conversation × mentioned_at) and `conversation_participants`
(user × conversation); mention parsing in the private-note composer, a dedicated *Mentions* inbox
view, and notification fan-out to participants on every SLA event and status change.

**Stept:** private notes exist (`message.visibility = note`), but no mention parsing, no participant
set, no "notify me on this thread" — `grep -ri "participant\|mention" backend/app` finds only
`notification.py` boilerplate and an unrelated RAG hit.

**Why P0:** this is the *only* collaboration primitive in a shared inbox. Beyond two agents, escalation
means "@alex can you look" and today that message reaches nobody. It's also the substrate for
notification preferences (§2.5) and for the participants fan-out that Chatwoot's SLA events use.

**Build:** `conversation_participants` + `mentions` tables; parse `@member` on note create → add
participant + mention row + notification + realtime push on `ws:{workspace_id}`; a `participating=true`
filter on the conversation list; auto-add assignee and anyone who posts a note.

---

### 1.3 Saved views / custom filters · **M**
**Chatwoot:** `custom_filters` (name, `filter_type` conversation|contact|report, `query` jsonb, per-user),
plus a full filter DSL (`app/services/filter_service.rb` + `app/services/filters/`) with typed
operators, and a `customviews` route in the dashboard.

**Stept:** `listConversations` accepts `status[]` and `assignee` and nothing else
(`frontend/src/features/inbox/api.ts:47`).

**Why P0:** at 100+ conversations/day, "unassigned + tag:billing + priority≥high + inbox:email" *is* the
product. Without it agents scroll. It's also the reporting drill-down mechanism and the audience
picker for campaigns and macros — one filter engine, four consumers.

**Build:** a shared filter DSL over conversations/contacts (attribute, operator, value, and/or) that
reuses `segments.contact_matches()`; persist as `saved_views` (per-user + shared); expose in the inbox
sidebar. Pairs tightly with §1.6 — typed attribute definitions are what make the operator list
non-garbage.

---

### 1.4 Bulk actions in the inbox · **S/M**
**Chatwoot:** `app/javascript/dashboard/components/widgets/conversation/conversationBulkActions/`
(agent/team/label/status), `api/bulkActions.js`, `composables/chatlist/useBulkActions.js` and
`useBulkActionsHotKeys.js`, plus a command bar (`routes/dashboard/commands/commandbar.vue`) with
go-to and action commands.

**Stept:** no multi-select anywhere in `features/inbox/components/`. The shadcn `command.tsx`
primitive is vendored but unused — no palette is wired.

**Why P0:** triage is a bulk operation. Also the cheapest morale win in this list.

**Build:** checkbox selection in `ConversationListPane`, a bulk action bar (assign / team / tag / status /
priority / run macro), one `POST /conversations/bulk` endpoint, and `j`/`k`/`e`/`a` hotkeys. Wire
`⌘K` to a go-to palette while you're in there — the component is already sitting in the repo.

---

### 1.5 Reports that a manager would accept · **M/L**
**Chatwoot:** two report APIs. v2 `reports#` — summary, bot_summary, agents, inboxes, labels, teams,
conversations, conversations_summary, **conversation_traffic** (hour×weekday heatmap), **drilldown**
(click a number → the conversations behind it), bot_metrics, inbox_label_matrix,
**first_response_time_distribution**, outgoing_messages_count; `summary_reports#` per
agent/team/inbox/label/channel; `live_reports#` (current open/unassigned/waiting, grouped); CSV
download on CSAT and applied-SLA. Backed by `reporting_events` + a daily
`reporting_events_rollups` table (account × date × dimension × metric → count, sum, **sum_business_hours**).

**Stept:** one endpoint, `GET /reports/overview` — `services/reports.py` has `_by_day`, `_by_channel`,
`_by_agent`, `_csat`, `_ai_stats`. Good bones, one screen.

**Why P0:** this is the screen the buyer looks at. Missing per-team/per-label breakdowns, missing
drill-down, missing CSV export, missing SLA-attainment reporting (you have the `sla_events` — nothing
reads them into a report).

**Build, in order:** (a) dimension breakdowns team/label/inbox + a shared `dimension` query param;
(b) CSV export on every report; (c) drill-down — every aggregate returns a filter payload that opens
the conversation list (this is why §1.3 comes first); (d) SLA attainment + breach report off
`sla_events`; (e) FRT distribution histogram + traffic heatmap; (f) **only then** the rollup table —
Chatwoot's `reporting_events_rollups` is a scale fix, and you don't have the scale yet.

---

### 1.6 Typed custom-attribute definitions · **S/M**
**Chatwoot:** `custom_attribute_definitions` — display name, key, `attribute_display_type`
(text/number/currency/percent/link/date/list/checkbox), `attribute_values` (list options),
`default_value`, `regex_pattern` + `regex_cue`, `attribute_model` (conversation|contact|company).
Values live in the record's `custom_attributes` jsonb.

**Stept:** `contact.attributes` and `conversation.attributes` are untyped `PortableJSON`.

**Why P0:** untyped JSON has no edit UI, no validation, no filter operators, and no reporting
dimension. It is the missing type system under §1.3 filters, segments, campaign audiences, and the
contact sidebar. Small table, disproportionate unlock.

---

### 1.7 Contact merge · block · CSV import/export · **M**
**Chatwoot:** merge action (moves conversations, messages, contact_inboxes, notes; deep-merges
attributes), `contacts.blocked` (inbound silently dropped), and a real import pipeline —
`data_imports` with validate_source / start / retry / abandon / error_logs / skip_logs,
`data_import_items` + `data_import_mappings` + `data_import_errors`, plus contact export.

**Stept:** none of the three.

**Why P0 — and this one is an adoption gate, not a feature:** **nobody can migrate onto Stept.**
Every prospect already has contacts in Intercom, Zendesk, or Chatwoot, and there is no door. Merge is
inevitable the moment you run more than one channel (same human arrives by email and by widget →
two contacts). Blocking is the only answer to a spammer.

**Build:** `POST /contacts/{id}/merge` (target wins scalars, deep-merge attributes, reparent
conversations/notes/events/contact_inboxes in one transaction, audit it); `contact.blocked` checked in
the inbound path of every channel adapter; a CSV import with a column-mapping step, per-row error log,
and resumable batches. An Intercom/Chatwoot-shaped importer preset would be a strong launch asset.

---

## 2. P1 — visible competitive holes

### 2.1 FAQ mining: conversations → approved answers · **M/L** ← *highest-leverage AI item*
**Chatwoot** (`enterprise/app/services/captain/llm/conversation_faq_service.rb`, `faq_observations`,
`faq_suggestions`, `assistant_responses`):

1. Only mine conversations where **a human actually replied** (`first_reply_created_at` present) — so
   it learns from real answers, never from the bot's own output.
2. LLM extracts Q/A candidates from the thread.
3. Embed each candidate; discard if it matches an already-**approved** answer (known) *or* a
   previously **dismissed** suggestion (already rejected) — cosine distance ≤ 0.3, top-5.
4. Otherwise merge into an open suggestion (`source_count += 1`) or create one.
5. A human approves → it becomes an `assistant_response`: a curated Q/A pair, editable, and the
   **primary** retrieval target.

**Stept:** search analytics tells you *what was asked* and *what returned nothing*. Nothing authors
the answer, and there is no curated-answer layer above the chunk index.

**Why it matters:** this is a compounding loop — support volume becomes knowledge automatically, with
a human gate, and `source_count` ranks the queue by how often the question actually comes up.
Stept's retrieval is better than Chatwoot's (§4.5), but better retrieval over a stale corpus loses to
worse retrieval over a corpus that heals itself.

**Build:** `faq_observations` + `faq_suggestions` tables, a `mine_conversation_faqs` task on resolve,
the dedup-by-embedding router above, and a review queue page (approve / edit / dismiss) sorted by
`source_count`. Ship together with §2.2.

---

### 2.2 Curated answers layer (pre-LLM Q/A) · **M**
Chatwoot's `assistant_responses` are approved Q/A pairs searched *before* — in fact, **instead of** —
raw document chunks (see `faq_lookup_tool.rb`; their FAQ tool searches only approved responses). Onyx
calls the same thing "standard answers"; it's already on Stept's roadmap.

Give Stept a `curated_answers` table (question, answer, embedding, status, source doc link, edited
flag) that retrieval consults first and blends into the RRF candidate set with a strong boost. Cheap,
fast, deterministic, auditable — and the destination for §2.1's approvals.

---

### 2.3 WhatsApp template management · **M**
Stept currently **fails** on any reply outside the 24h session window — correctly, with a clear
error, but that's a dead end for the user. Chatwoot syncs templates
(`GET graph/v14.0/{business_account_id}/message_templates`, cursor-paginated, cached on the channel),
exposes `POST inboxes/:id/sync_templates` + a template picker with variable filling, and uses templates
to deliver CSAT over WhatsApp.

For anyone actually running WhatsApp support this is an operational blocker, not a nicety. Small
relative to its unblock value.

---

### 2.4 Email channel, properly · **L**
**Chatwoot:** per-inbox IMAP + SMTP with Google/Microsoft **OAuth** refresh, ActionMailbox reply
threading (Message-ID / In-Reply-To / References), a forward-to address, `email_templates` (per
template_type, per locale, per inbox), branded email layouts, `continuity_via_email` on the widget,
email transcript on demand, and account-level email rate limiting.

**Stept:** webhook ingress only (`app/channels/email.py`), no IMAP, no OAuth, no templates.

Email is still the highest-volume support channel and the one most self-hosters need. Sequence:
IMAP/SMTP with app passwords → threading headers → templates + branding → OAuth providers.

---

### 2.5 Notification preferences + web push · **M**
**Chatwoot:** `notification_settings` (per-user × account, `email_flags` / `push_flags` bitfields per
event type), `notification_subscriptions` (browser Web Push / FCM), snooze, read-all, unread count,
destroy-all.

**Stept:** in-app notifications only, no preferences, no push, no email digests.

Agents don't sit in the dashboard. Without push or email, SLA breach notifications and mentions
(§1.2) reach nobody — which retroactively devalues both.

---

### 2.6 i18n framework (dashboard + widget) · **M** for the framework
Chatwoot ships **57 locales** for the dashboard, **57** for the widget, **56** for the survey app.
Stept is hardcoded English with no i18n layer at all.

Do the *framework* now, not the translations. Retrofitting message extraction across
~380 frontend tests and 186 widget tests later costs several times what it costs today, and locale
coverage is the single easiest thing to crowdsource in an OSS project. The widget matters most —
that's the surface end users see.

---

### 2.7 Assignment policies + agent capacity · **M**
**Chatwoot:** `assignment_policies` (per inbox: `assignment_order` round_robin|balanced,
`conversation_priority`, `fair_distribution_limit` + `fair_distribution_window`,
`exclude_older_than_hours`, enabled) and `agent_capacity_policies` + `inbox_capacity_limits`
(max concurrent conversations per agent per inbox, `exclusion_rules` jsonb).

**Stept:** plain round-robin.

Round-robin hands the 30th conversation to an agent already drowning in 29. Load-aware assignment is
the first thing any team with >5 agents asks for.

---

### 2.8 Scenarios: sub-agent handoff with scoped tools · **M**
**Chatwoot** `captain_scenarios`: title, description, `instruction`, and a **tool subset**, with the
root assistant exposing each scenario as a `handoff_to_scenario_{id}_{slug}_agent` tool. Tools are
referenced inline in the instruction as `[Add Note](tool://add_contact_note)` and resolved/validated
on save.

**Stept:** one flat system prompt + one tool list per agent.

Scenarios are how you get *reliable* multi-workflow agents — "process a refund" gets its own
instructions and only the three tools it's allowed to touch, instead of one 4000-token prompt trying
to cover eight workflows. It slots naturally on top of Stept's existing engine, and it composes
unusually well with approval gates: gate the handoff, not just the tool call.

---

### 2.9 Per-feature LLM model routing · **S**
**Chatwoot** `lib/llm/feature_router.rb` + `lib/llm/config.rb`: every feature (`assistant`, `copilot`,
`summarize`, `rewrite`, `label_suggestion`, `conversation_faq_generation`, `translation`, …) resolves
its own model, with a per-account override map and a documented default per feature.

**Stept:** one workspace default + a per-agent `model_ref`.

Cheap tasks (label suggestion, query rewriting, rerank) don't need the flagship model. At volume this
is the difference between a viable and a non-viable AI bill, and it's an afternoon's work on top of
the existing provider registry.

---

### 2.10 Conversation outcomes table · **M**
**Chatwoot** `conversation_outcomes` — per AI episode: `first_captain_reply_at`, `last_captain_reply_at`,
`captain_reply_count`, `first_human_reply_at`, `handoff_at`, **`handoff_reason_category`**,
`resolved_at`, `csat_rating`, `csat_received_at`, `episode_trigger`, `started_at`/`ended_at`.

Stept derives AI stats from `AgentRun` at query time. A denormalized outcome row per episode is what
makes "AI resolved 62% of conversations, handed off 38% — here's *why* it handed off, bucketed" a
real dashboard instead of a rough ratio. It's also the honest answer to the deflection-rate question
every buyer asks, and it pairs with §2.1 (a handoff reason is a FAQ candidate).

---

### 2.11 Dashboard apps (iframe sidebar) · **S**
`dashboard_apps` (title, `content: [{type:'frame', url}]`) renders customer-owned iframes as tabs in
the conversation sidebar, posted the conversation + contact context. Trivial to build, and it's the
escape hatch that stops people forking you to show their own order data next to a ticket.

---

### 2.12 Agent bots / bot API · **S/M**
`agent_bots` (name, `outgoing_url`, `bot_config`, `secret`, access token) + `agent_bot_inboxes`, with
conversations assignable to a bot. Third parties can build bots against Chatwoot without touching the
codebase. Stept's AI agents are in-process only — the seam matters more than the feature.

---

### 2.13 Integrations framework + first-party set · **M** framework, **S** each
`integrations_hooks` + `config/integration/apps.yml`: slack (bidirectional + unfurl), linear
(create/link/search issues from a conversation), notion, shopify (order lookup in the sidebar),
dialogflow, google_translate, dyte (video), leadsquared (CRM), openai. Stept has Slack as a *channel*
but no integration registry.

Build the registry first (hook model, per-hook encrypted config, event dispatch), then Linear and
Shopify — those two are the ones people ask for by name.

---

### 2.14 Pre-chat form builder + widget config depth · **M**
Chatwoot's `channel_web_widgets`: `pre_chat_form_enabled` + `pre_chat_form_options` (field builder),
`continuity_via_email`, `allowed_domains`, `hmac_mandatory`, `lock_to_single_conversation`,
`allow_messages_after_resolved`, `enable_email_collect`, `reply_time`, `welcome_title`/`welcome_tagline`,
per-feature flags (attachments/emoji/end-conversation), pop-out window, locale.

Stept has the identity gate and an accent color. `allowed_domains` and `hmac_mandatory` in particular
are security posture, not polish.

---

### 2.15 Help center v2 · **M/L**
**Chatwoot:** `portals` (multiple per account, `custom_domain` + `ssl_settings` provisioned through
Cloudflare, config, homepage_link, page_title, header_text, archived), `categories` (nested via
`parent_category_id`, per-`locale`, icon + color, reorderable), `articles` (per-`locale`,
`associated_article_id` linking translations, `meta` jsonb for SEO, `position`, **`draft_title` /
`draft_content`** so edits don't publish, `views`, related articles), `sitemap.xml`, a tracking pixel,
and **`/hc/:slug/articles/:slug.md`** — a markdown endpoint for LLM/agent consumption.

**Stept:** one portal, flat collections, no locales, no custom domain, no SEO meta, no draft/published
split on the body, no sitemap.

Prioritize: draft/publish bodies → SEO meta + sitemap → locales + translation links → custom domain →
nested categories. (The `.md` endpoint is ~20 lines and a nice touch for the AI-crawler era.)

---

## 3. P2 — breadth and enterprise

| # | Gap | Chatwoot | Effort | Note |
|---|---|---|---|---|
| 3.1 | **Voice channel** | Twilio Voice: inbound/outbound, `calls` table, conference manager, hold/transfer, recording + transcript, in-browser softphone via Twilio token; plus WhatsApp Calling (accept/reject/terminate/recording) | **XL** | Real "one inbox" table stakes, but a category of its own. Don't start it until §1 is done. |
| 3.2 | **Companies (B2B)** | `companies` (domain, attributes, contacts_count, last_activity_at), auto-association by email domain, business-email detection, company conversations/notes/attributes | **M** | Promote to P1 if the target market is B2B SaaS. |
| 3.3 | **Agent-assist toolbelt** | Captain tasks: `rewrite`, `summarize`, `reply_suggestion`, `label_suggestion`, `follow_up`, conversation summary, message `translate`, contact-attribute + contact-note extraction, audio transcription of voice notes | **S each** | Stept has copilot-suggest and editor-AI. Each of these is a small addition on the same rails; ship them as one wave. |
| 3.4 | **Auto-resolve stale conversations** | Three modes: `disabled` / `legacy` (pure timeout) / **`evaluated`** (LLM judges whether it's actually resolved before closing) | **S** | The evaluated mode is the good idea. |
| 3.5 | **Message reports** | `captain_message_reports` — agent flags a bad AI reply with a structured `report_reason` + description, into a review queue | **S** | Stept has 👍/👎; a reason code plus a queue turns feedback into a work item. |
| 3.6 | **Multilingual retrieval** | `TranslateQueryService` translates the query into the account language before searching | **S** | Cheap win on top of Stept's existing query-rewriting stage. |
| 3.7 | **SAML SSO / OIDC** | `account_saml_settings` (sso_url, certificate, entity IDs, **role_mappings**) | **M** | Enterprise gate. |
| 3.8 | **MFA + session management** | TOTP + backup codes; `user_sessions` (IP, UA, browser, device, city/country, last_activity) with list + revoke | **S/M** | Stept's refresh tokens already carry `user_agent` + `ip` — most of the substrate exists. |
| 3.9 | **Liquid templating** | `Liquidable` + drops across canned responses, macros, campaigns, email templates | **S** | Stept does `{{contact.name}}` substitution; a real template layer with filters and conditionals is a modest step up. |
| 3.10 | **Article/help-center AI** | Article writer, help-center curation, article translation, website analyzer + auto-generated starter help center at onboarding, widget tagline generation | **M** | Stept has editor AI (draft/outline/improve). The *onboarding* angle — analyze the customer's site, generate a starter KB — is the interesting part. |
| 3.11 | **Platform API** | `platform_apps` + permissibles: provision accounts/users/bots via API | **M** | For people running Stept as a service. |
| 3.12 | **TikTok / Twitter channels** | `channel_tiktok`, `channel_twitter_profiles` | **M** | Low demand; skip unless asked. |

---

## 4. Where Stept is already ahead — and how to press it

These are real, source-verified advantages. Several are under-marketed.

### 4.1 Approval gates — no equivalent anywhere in Chatwoot
Chatwoot's AI either acts or hands off; there is no pause-for-human-approval anywhere in `captain/`.
Stept can suspend a tool call mid-run, route it to a human, and resume with full trace continuity.

**Press it:** extend gates to **scenario handoffs** (§2.8) and to the in-app assistant's page actions,
so a policy can be "the AI may draft the refund but a human presses send." Add per-tool
approval thresholds (auto-approve refunds < €50). This is the single feature that makes an AI agent
deployable in a regulated or high-trust context, and nobody else in the OSS space has it.

### 4.2 The entire DAP layer — Chatwoot has zero
Tours v2 (6 step types, driven mode, self-healing selectors + breakage telemetry, frequency/schedule/
priority), checklists, in-product surveys, the WXT Chrome recorder with side panel and
`chrome.debugger` trusted input, `packages/dom-capture`. Chatwoot has no tours, no checklists, no
in-product surveys, no recorder — this isn't a gap in their product, it's a category they aren't in.

**Press it — and protect it.** Stept isn't "Chatwoot plus extras," it's Intercom + Fin + Pendo in one
MIT repo. The §1 backlog is table-stakes catch-up; it should be *timeboxed* so it doesn't eat the
category lead. From the parking lot in `docs/research/dap-competitors.md`, the highest-value next
steps are **goals + A/B testing** (turns tours from a feature into a measurable program) and
**no-code event trackers** (click/usage tracking → feature-adoption analytics, which is what Pendo
actually sells). Also: you already collect selector-breakage telemetry — surface it as a "broken
tours" queue with alerts. Nobody else can offer that because nobody else has the healing engine.

### 4.3 In-app assistant that acts on the visitor's screen
Client-executed page tools (`page_snapshot/find/read/act/navigate/scroll/wait`, `show_guide`,
`show_steps`) deferred to the browser over the existing pause/resume machinery, consent-gated, with
index→durable-`Target` conversion so an AI-authored walkthrough survives re-renders. Neither Chatwoot
nor Intercom Fin does this without installing an extension.

**Press it:** close the loop — when the assistant successfully walks someone through a task, offer to
**save it as a tour**. Support conversation → reusable onboarding asset is a loop no competitor can
run, and it's the same shape as Chatwoot's FAQ-mining loop (§2.1) applied to your differentiator.
Also add an action-replay/audit view: "here is exactly what the AI clicked" is what makes buyers
comfortable enabling it.

### 4.4 Real multi-provider AI with BYO keys — Chatwoot's is OpenAI-shaped
Chatwoot uses RubyLLM (multi-provider in principle) but configures only `openai_api_key` /
`openai_api_base`, keys the whole thing off a single `CAPTAIN_OPEN_AI_API_KEY` installation config,
pins defaults to `gpt-*`, and hardcodes `gpt-5.2` for v2 assistants. Stept has per-workspace providers
(OpenAI / Anthropic / Google / Ollama / any compatible) with encrypted keys, a model catalog, and a
deterministic mock provider.

**Press it:** self-hosters who can't send customer data to OpenAI can run Stept's AI on Ollama today
and can't run Chatwoot's at all. Say that in the README, with a working local-model quickstart. Then
add §2.9 (per-feature routing) so the story is "the right model per task, on your infrastructure."

### 4.5 Retrieval quality
Chatwoot's Captain retrieves by cosine similarity over **approved Q/A pairs only** — documents are
converted into Q/A pairs at ingest and the raw chunks are never searched (`faq_lookup_tool.rb`,
`search_documentation_service.rb`). Stept does hybrid dense+FTS with RRF, neighbor expansion, recency
decay, source boost, LLM rerank, intent classification, query rewriting, multi-query expansion, a
title leg, BM25 blend, and token-budgeted query-focused compression.

Stept is clearly better on long-tail questions. The honest caveat: Chatwoot's curated layer is better
on *quality control* for the head. §2.1 + §2.2 give you both — that's the whole argument for
prioritizing them.

### 4.6 SLA, custom roles, and audit log are MIT-core here and paid there
`sla_policies`, `applied_slas`, `sla_events`, `custom_roles`, `audits`, `agent_capacity_policies`,
`companies`, `calls`, and all of Captain live in Chatwoot's `enterprise/` directory under a
commercial license.

**Press it:** this is a positioning weapon and it's currently invisible. A comparison table in the
README — *"SLA policies: Stept ✅ MIT · Chatwoot 💰 Enterprise"*, same for custom roles and audit log —
is one afternoon and reframes the whole comparison.

### 4.7 Portability and test posture
Same models run on SQLite *and* Postgres; dev and test need zero external services; 1731 tests
including deterministic e2e against a mock AI provider. Chatwoot needs Postgres + Redis + Sidekiq to
boot at all. `git clone && make dev` with no Docker is a genuine contributor-acquisition advantage —
put it at the top of the README.

### 4.8 Typed contract end-to-end
FastAPI → OpenAPI → generated TS client, plus a build-failing schema-drift guard
(`src/api/schema-drift.ts`). Chatwoot's Vue dashboard talks to untyped JSON. Under-marketed, and a
real reason integrators will prefer your API.

### 4.9 Also ahead, smaller
Webhook delivery log + signing (Chatwoot's webhooks are fire-and-forget); Twilio signature validation
(Chatwoot **skips** it — see `docs/research/chatwoot-gaps.md` §1.2); SSRF guards on knowledge
connectors; search analytics with zero-result tracking; the unified widget "experiences" bootstrap
that serves tours, checklists, surveys, and campaigns from one eligibility call.

---

## 5. Where Chatwoot wins that isn't a feature

Worth naming so it doesn't get mistaken for a backlog item: **ecosystem**. 57 locales, published
iOS/Android apps (separate repos), a large integration catalog, years of docs and SEO, and a big
installed base. You close that with §2.6 (i18n framework), §2.13 (integration registry), and time —
not by out-featuring them.

---

## 6. Suggested sequencing

| Wave | Contents | Rationale |
|---|---|---|
| **A** | §1.1 business hours · §1.6 attribute definitions · §1.4 bulk actions + ⌘K · §2.9 model routing | All S/M, no interdependencies, immediately felt. Business hours makes SLA honest; attribute definitions unblock wave B. |
| **B** | §1.3 saved views/filter DSL · §1.2 mentions + participants · §1.7 merge/block/import · §2.5 notifications + push | The daily-driver core. Filters need B's attribute definitions; notifications need mentions to be worth having. |
| **C** | §1.5 reports (a–d) · §2.10 conversation outcomes · §2.3 WhatsApp templates | The buyer-facing screen, plus the AI-deflection substrate. Drill-down needs wave B's filters. |
| **D** | §2.1 FAQ mining · §2.2 curated answers · §2.8 scenarios | The AI wave. Highest strategic value; deliberately after the operational floor is in. |
| **E** | §2.6 i18n framework · §2.14 widget config · §2.15 help center v2 · §2.11 dashboard apps | End-user-facing surfaces and the extension seams. |
| **F** | §2.4 email properly · §2.7 assignment policies · §2.13 integrations registry · §2.12 agent bots | Depth and ecosystem. |
| **—** | §4.2 DAP: goals + A/B, event trackers, broken-tour queue | **Interleave, don't defer.** One DAP item per wave keeps the category lead moving while the catch-up work lands. |
