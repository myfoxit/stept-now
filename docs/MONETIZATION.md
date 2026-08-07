# Stept → $5k MRR: the plan and what's missing

Synthesized from teardowns of Chatwoot, Plausible, Cal.com, Typebot, Papermark, Documenso,
Formbricks, Novu, Trigger.dev and Intercom/Fin pricing (2026-08). This is the strategy doc;
`docs/INTEGRATIONS-CONTRACTS.md` is the current build wave that closes the #1 product gap.

## The honest diagnosis

Stept's **product breadth is already competitive** — 10 channels, RAG agent, help center,
tours/DAP, automation, MCP. What blocks revenue is not features, it's three things in order:

1. **You cannot accept money yet.** No Stripe, no plan entitlements, no hosted multi-tenant
   signup. This is ~70% of the path to $5k and it is infrastructure, not product.
2. **Integration depth converts trials** — email done properly + one-click OAuth. This is the
   wave in flight. Support tools live or die on email; every serious buyer tests it first.
3. **Trust + demonstrable AI resolution.** A support inbox is a PII vault; EU SMBs (your
   natural self-host-curious buyers) won't pay without a DPA/GDPR story, and the "undercut Fin"
   pitch collapses if the agent doesn't visibly resolve tickets.

Everything else (more channels, more connectors) is secondary to these three.

## Monetization design (recommended)

**License:** AGPLv3 core + a commercial `/ee` directory (the Cal.com / Formbricks / Chatwoot
pattern). AGPL deters closed-source resellers and keeps you eligible for awesome-selfhosted and
the self-host marketplaces. Add a contributor CLA so you can relicense `/ee`. Do **not** go
Fair-Source/FSL yet — it forfeits the "open source" label and directory listings; Typebot only
switched after real scale.

**Free, self-hosted, forever (all operational features):** every channel, unlimited agents,
knowledge + RAG, help center, automation, tours, **and the AI agent with bring-your-own API
key**. Make "self-host your own Fin for free" the headline — it is the sharpest contrast with
Chatwoot, which paywalls Captain AI entirely and is resented for it.

**Paid EE (self-host license key, flat ~$99/mo or $950/yr per workspace):** SSO/SAML, audit
logs, custom roles, white-label / remove widget branding, SLA management, priority support. The
universal "enterprise tax" trio is SSO + audit logs + white-label — gate exactly those.

**Cloud (the real engine):**

| Plan | Price | Line |
|---|---|---|
| Free | $0 | 1 seat, widget-only, "Powered by Stept" badge (the viral loop) |
| Pro | $19/seat/mo | all channels, 500 AI resolutions/mo bundled |
| Business | $49/seat/mo | EE features + 2,000 resolutions/mo |
| AI overage | ~$0.30–0.49/resolution or $25 per 1,000 credits | "half of Fin, aligned to value" |

The anchor for every sales page: **a 5-seat team pays ~$95/mo on Stept vs $1,000–1,500/mo on
Intercom + Fin** (seats $85–139 + Fin $0.99/resolution, 50-outcome minimum), and Chatwoot can't
match the AI story self-hosted. Underlying LLM cost per resolution is cents → ~90% margin on
bundled AI.

**Shape of the first $5k MRR:** ~35 cloud teams × ~$110 avg + ~10 EE licenses × $99 + a trickle
from marketplace rev-share. At a realistic ~3% activated-team→paid conversion (Papermark's
number) that needs ~1,500 activated teams — reachable from one good launch + comparison SEO.

## What's missing — ranked build order to $5k MRR

| # | Item | Effort | Why it's here |
|---|---|---|---|
| 1 | **Stripe billing + entitlement layer** (plan → seat limits, feature flags, metered AI-credit ledger) | M | Can't charge without it. `AgentRun` already counts resolutions — that's your meter substrate |
| 2 | **Hosted multi-tenant cloud + self-serve signup** (prod deploy, email infra, backups) | L | The main revenue engine; most $5k comes from cloud, not licenses |
| 3 | **Email done properly + one-click OAuth** (this wave) | L | The #1 trial-conversion gap for support tools |
| 4 | **License split** AGPL + `/ee` + CLA | S | Unblocks selling EE and marketplace listings |
| 5 | **Pricing page + 5 comparison pages** (vs Intercom, Chatwoot, Zendesk, Crisp/Tidio, "open-source Fin alternative") | M | The single most-proven OSS acquisition channel; compounding, free |
| 6 | **Trust pack**: ToS, privacy, **DPA + subprocessor list**, security page | S | Non-negotiable for EU SMB buyers; SOC2 not needed at $5k |
| 7 | **Onboarding to first value**: widget install wizard, Gmail/IMAP forwarding setup, KB import, demo data | M | Conversion is docs→deploy→signup, not stars |
| 8 | **Deploy-anywhere**: one-line compose, Coolify/Railway templates, **Elestio + PikaPods listings (they pay you 10–30% rev-share)**, awesome-selfhosted PR, AlternativeTo/OpenAlternative | M | Distribution + a little passive revenue |
| 9 | **Show HN launch + build-in-public cadence** | S | The step-change (Chatwoot: 1k stars in 2 days; Plausible: 166 trials/wk from one post). Needs 3,5,6 live first |
| 10 | **AI resolution proof**: public benchmark/demo + per-resolution analytics dashboard | M | Justifies charging for AI; the thing Fin bills $0.99 for |

## Distribution notes

- **"X alternative" SEO** is the proven OSS channel (Plausible, Papermark, Formbricks all
  credit it). Own the SERP for "open source Intercom alternative", "open source Fin alternative",
  "self-hosted Chatwoot alternative with AI".
- **Self-host marketplaces pay you:** Elestio ~30% of hosting revenue, PikaPods 10–20%. List
  early — free distribution + passive income.
- **Vertical app stores beat generic directories** for support software — Gorgias built a
  17k-brand company almost entirely on the Shopify App Store. Being *in* the Slack App Directory
  and (later) Shopify is discovery, not just integration.
- **Stars are vanity;** the funnel that converts is docs → deploy → cloud signup.

## Where this doc meets the code

The integrations wave (`docs/INTEGRATIONS-CONTRACTS.md`) delivers item #3 and the OAuth
framework that later powers a Shopify/Slack app-store presence (#8) and the "connect your data"
onboarding (#7). Billing (#1) and cloud (#2) are the next two waves and are the real gate — plan
them immediately after this one.
