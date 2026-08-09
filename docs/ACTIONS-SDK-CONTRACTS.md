# Actions SDK — Contracts (W12)

> The developer-facing half of the in-app assistant: a host page **registers
> functions** the agent may call. Where W8's `page_*` tools let the agent act on
> the page's DOM, a client action runs the developer's own code, in the page,
> with the signed-in user's session. Stept's server never touches the customer's
> API. Read `docs/IN-APP-ASSISTANT.md` first — this rides the same machinery.

## The developer surface

```html
<script>
  window.SteptSettings = { workspaceKey: 'wk_…' };
  // Works before loader.js loads: calls queue on window.Stept.q (Intercom-style).
  Stept('action', {
    name: 'invite_teammate',                       // ^[a-z][a-z0-9_]{0,47}$
    description: 'Invite a teammate by email',     // required, ≤500 chars
    params: {                                      // JSON-Schema object (subset), ≤4000 chars serialized
      type: 'object',
      properties: { email: { type: 'string', description: 'Email to invite' } },
      required: ['email'],
    },
    confirm: true,           // default true — in-chat "Run this?" card before executing
    approval: false,         // default false — true pauses the run for TEAM approval (existing gate)
    requiresIdentity: false, // default false — true: only offered when the visitor is HMAC-verified
    run: async ({ email }) => inviteTeammate(email),   // the handler; may return string | JSON-able
  });
  Stept('removeAction', 'invite_teammate');
</script>
```

- `params` accepts the same dependency-free JSON-Schema subset `CustomAction`
  uses (top-level `type: object`, `properties` with `type` +
  `description`/`enum`, `required`). A missing `params` means "no arguments".
- Handler return values: string kept as-is; other JSON-serializable values are
  JSON-stringified; `undefined` → `"done"`. Serialized result capped at 8000
  chars client-side (server backstops at 24000 like every page op). Handler
  exceptions and rejections become `{ok:false, error}` tool results — never
  thrown into the host page. Handlers race a 30s timeout.
- Re-registering a name replaces the def+handler. Registry mutations after boot
  re-advertise (see wire flow). The registry survives `shutdown`/`boot` cycles —
  the handlers belong to the page, not the widget instance (a logout→login
  re-boot keeps the page's verbs without re-running registration code).

## Naming

- Developer name: `^[a-z][a-z0-9_]{0,47}$` (48 max).
- LLM-visible spec name: `app_<name>` — prefix avoids collisions with builtins,
  `page_*`, and custom actions, and makes traces self-describing. Spec names are
  what appears in `AgentStep.name` and per-tool policies.

## Limits (server-enforced, mirrored client-side)

| Limit | Value |
|---|---|
| Defs per conversation | 20 (`MAX_DEFS`) |
| `description` | 500 chars |
| `params` serialized | 4000 chars |
| Stored defs payload total | 16000 chars |
| Action calls per run (`app_*`) | 10 (`MAX_ACTION_CALLS`) |
| Handler timeout (client) | 30 s |
| Result chars (client / server) | 8000 / 24000 |

Oversized/invalid defs are dropped at intake **silently per-def, reported in
bulk**: the page-context response echoes `accepted_actions: [names]` so the SDK
can `console.warn` what was rejected.

## Wire flow

1. **Loader → iframe app**: `MSG.PAGE_CONTEXT` payload gains
   `actions: ClientActionDef[]` (functions stripped — name/description/params/
   confirm/approval/requiresIdentity only). Pushed on READY, on SPA URL change
   (both existing), and now on any registry mutation.
2. **App → backend**: `POST /api/widget/conversations/{id}/page-context` gains
   `client_actions: ClientActionDefIn[] | null`. `null` = leave stored defs
   untouched (tri-state like `allow_actions`); `[]` = clear. Stored on
   `conversation.attributes["client_actions"]` as
   `{"defs": [...], "identified": bool}` — `identified` is
   `bool(contact.external_id)` at intake time (external identity only ever comes
   from a verified HMAC boot). **No migration.**
   Defs additionally ride `POST /conversations` and `POST …/messages` (same
   tri-state field), stored in the same transaction that triggers the run — so
   the FIRST agent turn already has the page's verbs, with no page-context race.
3. **Resolve** (`tools.resolve_agent_tools`): when the agent allows client
   actions, each stored def becomes a `ToolSpec(name="app_<name>")` in the plan,
   in `plan.client` (deferred), with its def in `plan.client_action_defs`.
   `requiresIdentity` defs are withheld when `identified` is false — withheld,
   not refused, so the model never proposes what it cannot do.
   Policy: explicit per-agent config (`app_<name>` key) wins, else
   `require_approval` if the def says `approval`, else `auto`.
4. **Engine**: `app_*` calls validate against the def's `params` schema
   (`validate_params`) and the per-run cap, then park exactly like a page op —
   `client_request` step, `awaiting_client`, op broadcast `copilot.op` with
   `{op: "action", args: {name, params, confirm}}`. `sweep_stale_client_waits`
   unchanged (a card nobody answers times out and the model says so).
5. **Iframe app**: op `action` with `confirm:false` → forwarded to the loader
   immediately. With `confirm:true` → an **action card** renders in the thread
   (name, description, pretty-printed params, Run / Not now); Run forwards to
   the loader, Not now answers `{ok:false, declined:true}` without touching the
   page. Reload mid-confirm re-renders the card from `GET copilot/pending`.
6. **Loader**: `MSG.COPILOT_OP` with `op === "action"` dispatches to the
   registry (never to `PageAgent`); missing handler →
   `{ok:false, error:"action '<name>' is not available on this page"}`.
7. **Result**: existing `POST copilot/result` → `submit_client_result` →
   resume. Untouched.

## Gating

- Agent settings block: `settings.client_actions = {"enabled": bool}` —
  **absent → enabled** (the developer registering actions is the opt-in;
  page-control stays opt-in because it touches other people's DOM). Explicit
  `false` withholds all `app_*` specs.
- Per-tool overrides in `agent.tools` work on `app_*` keys exactly like
  builtins (`auto` / `require_approval` / `disabled`).
- `confirm` is visitor-side UX; `approval` is the existing durable team gate.
  Both may be on: approval pauses first (policy check precedes client dispatch),
  then the visitor still confirms before execution.
- Sandbox runs: stored defs never exist on the ephemeral conversation, so
  `app_*` specs are absent; the engine still guards the branch.

## Trace

`AgentStep` rows are unchanged in shape: `client_request` with
`name="app_invite_teammate"`, `input` = LLM args, `output` = `{op_id, op:
"action", args}`. The dashboard trace viewer labels `op === "action"` steps as
"App action" (vs "Page op").

## npm packages (`packages/`)

- `@stept/js` — typed, SSR-safe wrapper: `loadStept(settings)` injects the
  loader from `settings.apiBase`, `stept(cmd, ...)` proxies to `window.Stept`
  with pre-load queueing, `registerAction(def)` / `removeAction(name)` typed
  helpers, full `ClientActionDef` types. Zero deps.
  (The name `@stept/widget` is held by the internal iframe app and is
  load-bearing in CI/Makefile/e2e filters — renaming is a publish-time decision,
  not worth the churn in this wave.)
- `@stept/react` — `<SteptProvider settings>` boots on mount (client-only
  effect), `useStept()` returns the command fn, `useSteptAction(def, deps?)`
  registers on mount / replaces on change / removes on unmount. Peer dep:
  react ≥18. Internal dep: `@stept/js`.
- Both build with `tsc` to `dist/` (esm + d.ts); dev `main`/`exports` point at
  `src/` for workspace linking and `publishConfig` swaps in `dist/` at pack
  time. Publishing itself is a follow-up (needs the npm org + 2FA/provenance
  decision).

## Tests (definition of done)

- **backend** `tests/agents/test_client_actions.py`: def normalization
  (name/limits/schema trimming, accepted echo); resolve merge + `identified`
  withholding + `enabled:false` + policy mapping (approval def → pause for
  approval); happy path park→result→final reply; declined result; per-run cap;
  unknown-name execution refused at validation; pending route returns the
  action op; page-context intake authz (foreign conversation 404) + `null`
  vs `[]` semantics.
- **widget** loader/action-registry unit tests + `copilot.test.ts` additions:
  defs ride page-context; confirm card gates dispatch; Not now posts declined;
  no-confirm dispatches straight; missing handler answers not-available;
  registry mutation re-advertises.
- **packages**: react hook lifecycle (register → replace → remove), queue-before-
  load behavior in `@stept/widget`.
- **e2e** (`e2e/`): script-tag page registers an action; conversation triggers
  `[[tool:app_…]]`; confirm card → Run; assert the handler ran and the reply
  contains the tool result. (Reuses the existing widget e2e harness.)

## Explicitly out of scope for W12 (→ W14+)

OpenAPI import, MCP client, server SDKs (`stept` pip / `@stept/node`),
generative UI cards, `Stept('setContext')`, npm publish pipeline.
