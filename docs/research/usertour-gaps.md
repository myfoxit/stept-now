# Usertour — source-level gap analysis

> Written 2026-08-18 from a fresh clone of `usertour/usertour` @ main (v0.9.2, 15 Aug 2026),
> reading `apps/server/src/mcp/**` line by line. Supersedes the two-line Usertour entry in
> `docs/research/dap-competitors.md`, which is **stale in two ways**: it says AGPL (they
> relicensed to MIT + a proprietary `LICENSE.enterprise`) and it predates v0.8.5–v0.9.2.

## What they are, so the comparison stays honest

Usertour is a **product-adoption platform** — "an alternative to Appcues, Userpilot, Userflow,
Userguiding, Chameleon, Pendo, WalkMe". Flows, checklists, launchers, banners, trackers,
resource centers, announcements. 2.3k stars.

No inbox. No conversations. No channels. No RAG. No AI support agent. No help center.

**Their entire product is the DAP slice of Stept (W7).** They are not a competitor to Stept;
they are a much better-finished competitor to one-fourteenth of it. Everything below is scoped
to that slice — outside it there is nothing to compare.

Licensing note worth keeping: their community code is MIT, but `LICENSE.enterprise` covers
"all source code files that contain 'enterprise' or 'ee' in their dirname, and any code
controlling feature, permission, role, license, branding removal, white-label enablement, or
plan enablement", and forbids production use without a paid subscription. Their SSO and admin
panel are the paid tier. **Stept is MIT throughout.** That is a real, defensible difference.

## The thing they actually do better: MCP as a first-class authoring surface

Their v0.9.1 MCP server is not a thin CRUD wrapper. It is designed around the observation that
*an LLM authoring product content will produce content that publishes green and never renders*,
and nearly every design decision falls out of defending against that.

### 1. `SERVER_INSTRUCTIONS` — a routing map in the initialize handshake

Their `server-instructions.ts` header says it outright:

> Every line here earns its place by a mistake agents actually made in zero-knowledge evals
> (not knowing to read the guide first, guessing non-flow `data` shapes, re-targeting blindly
> instead of diagnosing, treating a truncated list as complete).

So the instructions are a **routing map** — "for this intent, call this tool" — plus a short
list of facts that prevent specific observed failures ("a survey is a flow with question
blocks, there is no separate survey type"; "lists return `{items, nextCursor}` — page until
nextCursor is null before concluding something does not exist").

Stept's instructions were three sentences of prose naming tool families. **Cheapest, highest-
leverage thing on this page.**

### 2. `get_authoring_guide` — 83KB of contract, fetched on demand, by section

`authoring-guide.ts` is a sectioned document (`lifecycle`, `themes`, `flow-steps`, `targets`,
`conditions`, `start-rules`, `orchestration`, `sdk`, `surveys`, `announcements`,
`publish-requirements`, `icons`, …). Calling with no args returns the core sections plus a
table of contents; the agent then fetches the sections for its content type **in one array
call**.

The division of labor is the insight: the handshake instructions stay compact (they are paid
for on every connection), the deep contract is paid for only when authoring. Stept had neither
half.

### 3. Validate-before-publish, and immutable live versions

- `validate_content_version` — a dry run that "refuses configurations that would publish green
  and never render".
- A version that is live, **or has ever been live**, is immutable; you fork it with
  `create_content_version`. Unpublishing does not unlock it.
- `list_publish_history` — a permanent per-content publish/unpublish ledger with version,
  environment, actor, timestamp.

### 4. `diagnose_content` / `diagnose_user` — the best idea in the codebase

`diagnose-report.ts` (33KB) answers "why isn't my content showing?" by evaluating **the same
runtime gates the SDK uses** — published / identified / start_rules / frequency /
single_session / hidden / active_session / target — and annotating every condition in the tree
with `matched | unmatched | unknown`.

Two details worth stealing verbatim:

- The header comment draws a distinction most people never bother to: *"A condition states a
  FACT ('satisfied?'); a gate is a JUDGMENT ('blocks?'). Kept distinct so a hide condition
  being `matched` (it would hide) reads correctly."*
- Unmatched attribute leaves carry the user's **actual current value**, "so an unmatched
  condition explains itself without a separate get_user + date math".

And `diagnose_user` inverts it: "what would this user see right now" sorts every published
content into showing / queued / blocked, with slot races settled.

The MCP layer does **no re-derivation** — it overlays status onto facts the websocket service
already produced with the production evaluator. That is why the answer is trustworthy.

### 5. Tool annotations picked by verb prefix

`annotations.ts` is 48 lines and classifies every write tool by its name prefix:
`delete_|remove_|unpublish_|end_` → destructive, `update_|upsert_|restore_|publish_|add_` →
idempotent, everything else → additive create. `openWorldHint` is always false because every
tool acts on the caller's own project. Clients use this to decide what to confirm.

### 6. Analytics shaped for a readout, not a dashboard

`get_content_analytics`, `get_content_question_analytics` (defaults to last 30 days), and
`get_usage_overview` — "every content ranked by reach in one call; companyId + expand:
['users'] adds the account's member-by-member progress roster." Their docs then ship five
copy-paste analysis prompts on top.

## Gap matrix (DAP slice only)

| Capability | Usertour | Stept before this wave | Verdict |
|---|---|---|---|
| MCP content authoring (create/update/publish) | ✅ ~30 write tools | ❌ read + record only | **real gap — closed this wave** |
| MCP routing instructions in handshake | ✅ eval-derived | ❌ 3 sentences | **closed this wave** |
| On-demand sectioned authoring guide | ✅ 83KB | ❌ none | **closed this wave** |
| Schema introspection (`get_content_schema`) | ✅ | ❌ | **closed this wave** |
| Validate before publish | ✅ | ❌ | **closed this wave** |
| `diagnose_content` / `diagnose_user` | ✅ | partial (`tours_health`) | **closed this wave** |
| MCP analytics readout | ✅ per-type + overview | ❌ | **closed this wave** |
| Tool annotations | ✅ | ❌ | **closed this wave** |
| Immutable live versions + fork | ✅ | ❌ (`version` int bump) | open — needs a versions table |
| Publish history ledger | ✅ | audit log only | open — audit rows exist, no ledger view |
| Companies / accounts entity | ✅ + cross-entity segments | ❌ no model | open — B2B targeting hole |
| Environments (prod/staging) | ✅ | ❌ workspaces only | open — invasive, own wave |
| Per-content localization + AI translation | ✅ every type | articles only | open |
| Enterprise SSO (OIDC, force-SSO) | ✅ (paid tier) | ❌ social OAuth only | open |
| Resource center | ✅ | ❌ parked | open |
| No-code event trackers | ✅ | ❌ parked | open |
| Install verification page / admin panel | ✅ | ❌ | open |
| OAuth 2.1 + PKCE for MCP, CC plugin | ✅ | API keys + snippet | open — distribution |

## Where Stept is ahead inside the DAP slice

Worth recording so a future wave does not "achieve parity" by deleting an advantage:

- **`browser_*` — a real browser bridge.** Their docs tell you to pair with *Chrome DevTools
  MCP* to pick selectors. Stept has its own extension bridge: 16 DOM ops, `browser_record_start
  /stop` (author a tour by demonstrating it), `browser_run_tour`, CDP-trusted input, cross-worker
  routing. Nothing in their tree comes close.
- **Self-healing selectors + breakage telemetry + `tours_health`.** Their `diagnose_content` is
  a report; Stept's tours repair themselves and report red/yellow/green from real playback.
- **The in-app assistant (W8)** — driven "do-it-for-me" execution on the visitor's own screen.
- **Actions SDK (W12)** — the host page teaches the agent its own verbs.
- **Everything outside the slice**: inbox, 9 channels, RAG with citations, agent engine.

## What this wave took, and why

Ported: the routing map, the sectioned authoring guide, schema introspection, validate-before-
publish, the gate-based diagnosis (reusing Stept's *own* `deliverable_*` evaluator the way they
reuse theirs), the analytics readout shape, and the annotation doctrine.

Deliberately not ported: environments and immutable versions are the two that would touch every
content row and every delivery query — they are their own wave, not a bolt-on, and doing them
badly is worse than not doing them.
