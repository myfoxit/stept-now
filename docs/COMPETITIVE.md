# Stept — Competitive Feature Analysis

> **Superseded for Chatwoot by `docs/CHATWOOT-BACKLOG.md` (2026-08-07).** Every 🚧 row below shipped
> in W6; the Chatwoot columns here predate W6–W8 and Chatwoot's own Captain/voice work.
> The Onyx and DAP comparisons below are still current.

> Written 2026-07-31 from source-level analysis of fresh Chatwoot + Onyx clones
> (`docs/research/chatwoot-gaps.md`, `docs/research/onyx-gaps.md`) plus product
> knowledge of Intercom and the DAP leaders (Pendo, WalkMe, Appcues, Userpilot).
> Legend: ✅ shipped · 🚧 this wave (feature/competitive-parity) · 📋 roadmap · — not planned.

## 1. Support platform (vs Intercom, Chatwoot)

| Capability | Intercom | Chatwoot | Stept |
|---|---|---|---|
| Shared inbox (status/assign/teams/tags/notes) | ✅ | ✅ | ✅ |
| Web chat widget (identity HMAC, help center in widget) | ✅ | ✅ | ✅ |
| Email channel (out + inbound webhook, reply threading) | ✅ | ✅ (full IMAP) | ✅ (webhook ingress; IMAP 📋) |
| Slack channel | app | ✅ | ✅ |
| Telegram channel | — | ✅ | ✅ |
| API channel | ✅ | ✅ | ✅ |
| **WhatsApp (Cloud API, 24h window)** | ✅ | ✅ | 🚧 |
| **Facebook Messenger** | ✅ | ✅ | 🚧 |
| **Instagram DM** | ✅ | ✅ | 🚧 |
| **SMS (Twilio, signature-validated, delivery receipts)** | ✅ | ✅ | 🚧 (Chatwoot skips signature validation; we don't) |
| **LINE** | — | ✅ | 🚧 |
| Canned responses (+ placeholders) | ✅ | ✅ | ✅ |
| **Macros (multi-action shortcuts, personal/global)** | ✅ | ✅ | 🚧 |
| CSAT surveys | ✅ | ✅ | ✅ |
| **Message feedback (👍/👎 on AI replies)** | ✅ | — | 🚧 |
| Round-robin auto-assignment | ✅ | ✅ | ✅ |
| **SLA policies (FRT/NRT/resolution, breach events)** | ✅ | ✅ (EE) | 🚧 (wall-clock; business-hours math 📋) |
| **Campaigns: ongoing widget proactive messages** | ✅ (Outbound) | ✅ | 🚧 |
| **Campaigns: one-off scheduled sends (email/SMS/WA to segment)** | ✅ | ✅ | 🚧 |
| Automation rules (event/conditions/actions) | ✅ | ✅ | ✅ |
| Outbound webhooks (signed, deliveries log) | ✅ | ✅ | ✅ |
| Reports (volume, FRT, resolution, CSAT, per-agent) | ✅ | ✅ | ✅ |
| Help center (public portal + articles→RAG) | ✅ | ✅ | ✅ |
| Contacts, segments, events, custom attributes (JSON) | ✅ | ✅ | ✅ (typed attribute *definitions* 📋) |
| RBAC + custom roles, audit log, API keys | ✅ | partial | ✅ |
| Business hours per inbox | ✅ | ✅ | 📋 (widget office_hours config only) |
| Conversation participants/watchers, @mentions | ✅ | ✅ | 📋 |
| Contact merge / blocking | ✅ | ✅ | 📋 |
| Dashboard apps (iframe sidebar apps) | ✅ (Canvas Kit) | ✅ | 📋 |
| Pre-chat forms (field builder) | ✅ | ✅ | 📋 (identity gate exists) |
| Voice/TikTok channels, mobile SDKs | ✅ | some | — (v1) |

## 2. AI agent + RAG (vs Intercom Fin, Onyx)

| Capability | Fin | Onyx | Stept |
|---|---|---|---|
| Multi-provider LLM (OpenAI/Anthropic/Google/Ollama/compat + offline mock) | — (managed) | ✅ (litellm) | ✅ |
| Agent tools + custom HTTP actions (schema-validated, host-pinned) | ✅ | ✅ (OpenAPI) | ✅ |
| **Approval gates (human-in-the-loop pause/resume)** | partial | — | ✅ (differentiator) |
| Full run traces (steps, tokens, citations) | ✅ | ✅ | ✅ |
| Copilot reply suggestions | ✅ | — | ✅ |
| Hybrid retrieval (dense+lexical, RRF), neighbor expansion | ✅ | ✅ (Vespa) | ✅ (pgvector+FTS / SQLite fallback) |
| Recency decay + source boost | ✅ | ✅ | ✅ |
| Citations `[n]` with deep links | ✅ | ✅ | ✅ |
| **LLM rerank/selection pass (graceful fallback)** | ✅ | ✅ | 🚧 (Onyx's current default approach; cross-encoders 📋) |
| Sources: file upload, pasted text, URL lists, articles | ✅ | ✅ | ✅ |
| **Sitemap connector (index recursion, caps)** | ✅ | ✅ | 🚧 |
| **Recursive site crawler (same-site BFS, dedup, SSRF guard)** | ✅ | ✅ (Playwright) | 🚧 (httpx; JS rendering 📋) |
| **GitHub connector (docs/issues/PRs)** | — | ✅ | 🚧 |
| **Notion connector (block traversal)** | ✅ | ✅ | 🚧 |
| **Scheduled re-sync (per-source refresh interval) + pruning** | ✅ | ✅ (15s beat, refresh_freq) | 🚧 |
| **Search analytics (query log, zero-result rate, top queries)** | ✅ | ✅ (EE) | 🚧 |
| **AI deflection metrics (resolved vs handed off)** | ✅ | ✅ | 🚧 (from AgentRun + feedback) |
| Feedback→document boost mutation | — | ✅ | 📋 (feedback recorded; boost wiring next) |
| Connector checkpointing/incremental poll windows | — | ✅ | 📋 (full re-sync per run in v1) |
| Confluence/GDrive/Jira/Zendesk/50+ connectors | some | ✅ | 📋 (framework now supports adding each in ~a day) |
| Standard answers (canned pre-LLM matches) | — | ✅ | 📋 |
| Doc-level ACL at query time | — | ✅ | 📋 (workspace-scoped today) |
| Query expansion (multi-query weighted RRF) | ✅ | ✅ | 📋 |

## 3. DAP (vs Pendo, WalkMe, Appcues, Userpilot)

| Capability | Pendo/Appcues | Stept |
|---|---|---|
| Product tours (steps, selectors, placement) | ✅ | ✅ |
| No-code recorder (Chrome extension) | ✅ | ✅ |
| URL-match + audience targeting, seen-exclusion | ✅ | ✅ |
| Tour analytics (starts/completion/drop-off) | ✅ | ✅ |
| **Proactive in-app messages (URL + time-on-page triggers)** | ✅ | 🚧 (via campaigns — Intercom-style) |
| Checklists / onboarding hubs | ✅ | 📋 |
| NPS / in-product surveys | ✅ | 📋 (CSAT + message feedback exist) |
| Banners / hotspots / tooltips (standalone) | ✅ | 📋 |
| Feature adoption analytics (click/usage tracking) | ✅ | 📋 (contact events exist as substrate) |

## 4. This wave in one line each

1. **5 new channels** — WhatsApp Cloud API (verify challenge, X-Hub-Signature-256, 24h window, delivery receipts), Facebook Messenger, Instagram DM, Twilio SMS (X-Twilio-Signature validated, delivery callbacks), LINE (x-line-signature, profile enrichment).
2. **SLA policies** — FRT/NRT/resolution thresholds, applied-SLA lifecycle (active → hit/missed/active_with_misses), per-episode NRT breach events, assignee notifications, scheduler-driven scan.
3. **Macros** — Chatwoot's action vocabulary, personal/global visibility, per-action error collection, `{{contact.name}}` substitution.
4. **Campaigns** — ongoing widget proactive messages (client-evaluated URL/time-on-page triggers, fresh-visitor rule) + one-off scheduled sends to segment/tag/all audiences over email/SMS/WhatsApp.
5. **4 new knowledge connectors** — sitemap (index recursion), recursive crawler (same-site BFS, content-hash dedup, SSRF guard), GitHub (README/docs/issues/PRs with Onyx's file filters), Notion (block traversal → markdown).
6. **Scheduled re-sync** — per-source `refresh_minutes`, in-process scheduler (15s tick), deletion pruning on complete syncs, encrypted per-source credentials.
7. **LLM rerank** — Onyx-style selection pass over a widened RRF candidate set, hard timeout, graceful fallback to fused order.
8. **Search analytics + feedback** — every query logged (playground/widget/agent/copilot), zero-result + top-query reports, 👍/👎 on AI replies from both inbox and widget, AI deflection rate.
9. **Scheduler core** — `@scheduled` job registry + lifespan loop (also powers SLA scans and campaign dispatch).

## 5. Deliberate cuts (and why)

- **Business-hours SLA math / working-hours model** — needs the per-inbox weekly schedule model first; wall-clock SLAs ship now, the schedule model is the next increment.
- **WhatsApp template messages** — sending outside the 24h window requires Meta-approved templates; we fail with a clear error instead of silently dropping. Template sync/sending is a focused follow-up.
- **IMAP polling email** — webhook ingress (Mailgun/SES/Postmark-style) covers the common path; direct IMAP is a self-host nicety.
- **Cross-encoder rerank server** — Onyx itself defaults to no cross-encoder now; the LLM selection pass matches their current default without a model server.
- **Checklists/NPS/banners (DAP)** — real products, each needs widget UI + targeting + analytics; tours + campaigns land first, these reuse their rails next.
- **Per-doc ACL, connector checkpointing, 50-connector catalog** — framework seams are in place (secrets, per-source sync, pruning); breadth is incremental from here.
