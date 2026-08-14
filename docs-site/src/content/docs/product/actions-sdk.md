---
title: Actions SDK
description: Teach the AI assistant your app's own actions in one line — functions that run in the visitor's browser with their session, gated by confirm cards, team approval, and identity checks.
---

The assistant can answer from your knowledge base, play tours, and (with consent) click
through your UI. **Client actions** go one step further: you register a function, and the
assistant can *run it* — invite the teammate, apply the promo code, create the project —
in the visitor's browser, with the signed-in user's own session and permissions. Stept's
server never calls your API and never holds your users' credentials.

## One line, with the script tag

Start from the canonical [widget snippet](/product/widget/) — the queue stub in it is what
lets you register actions before the loader arrives:

```html
<script>
  window.SteptSettings = { workspaceKey: "wk_..." };
  window.Stept = window.Stept || function () { (window.Stept.q = window.Stept.q || []).push(arguments) };
</script>
<script src="https://your-stept-host/widget-assets/loader.js" async></script>
```

Then, anywhere after the stub (before the loader loads is fine — commands queue):

```html
<script>
  Stept('action', {
    name: 'invite_teammate',                    // ^[a-z][a-z0-9_]{0,47}$
    description: 'Invite a teammate to the current workspace by email',
    params: {
      type: 'object',
      properties: {
        email: { type: 'string', description: 'Email address to invite' },
      },
      required: ['email'],
    },
    confirm: true,                              // default true — "Run this?" card in the thread
    run: async ({ email }) => inviteTeammate(email),
  });
</script>
```

That's the whole integration. When someone asks *"can you invite sam@acme.io for me?"*,
the agent calls the action, the visitor sees a card with the exact arguments and a
**Run / Not now** choice, your function runs in their tab, and the agent reports the
result — all of it in the run's trace in your dashboard.

`Stept('removeAction', 'invite_teammate')` unregisters it (say, when the screen that
offers it goes away).

## With npm

:::note
`@stept/js` and `@stept/react` live in the repo
([`packages/`](https://github.com/myfoxit/stept-now/tree/master/packages)) and are **not
yet published to npm** — publish is imminent. The script-tag path above works today; until
the packages land on npm, install them from the repo or vendor the files.
:::

`@stept/js` (framework-free, SSR-safe):

```ts
import { loadStept, registerAction } from '@stept/js'

loadStept({ workspaceKey: 'wk_…', apiBase: 'https://app.stepped.ai' })
registerAction({ name: 'invite_teammate', description: '…', run: async (p) => … })
```

`@stept/react`:

```tsx
import { SteptProvider, useSteptAction } from '@stept/react'

function Billing() {
  useSteptAction(
    {
      name: 'apply_promo_code',
      description: 'Apply a promo code to the current subscription',
      params: { type: 'object', properties: { code: { type: 'string' } }, required: ['code'] },
      confirm: true,
      run: ({ code }) => billing.applyPromo(String(code)),
    },
    [], // re-register when these deps change; removed on unmount
  )
  return <PricingTable />
}
```

An action registered with `useSteptAction` exists only while its component is mounted, so
the assistant is never offered a verb the current screen doesn't have.

## The definition

| Field | | |
|---|---|---|
| `name` | required | `^[a-z][a-z0-9_]{0,47}$`. The model sees it as `app_<name>`. |
| `description` | required | What it does — the model plans with this. ≤500 chars. |
| `params` | optional | JSON-Schema object (`type`, `properties`, `required`, `enum`). Omit for no arguments. |
| `confirm` | default `true` | Show the visitor a Run / Not now card with the exact arguments first. |
| `approval` | default `false` | Pause the run until someone on **your team** approves from the inbox — the same durable gate custom tools use. |
| `requiresIdentity` | default `false` | Only offer this action when the visitor is [HMAC-identity-verified](/product/widget/#identity-verification). |
| `run` | required | `(params) => result`. Runs in the page. Return a string or any JSON-able value; throw to report failure. |

Results are truncated at 8&#8239;000 characters in the browser (with a 24&#8239;000-character
server-side backstop), handlers time out after 30&#8239;s, and a handler exception becomes
an error the model reads and explains — it never breaks your page.

If the visitor never answers a confirm card, it times out after 600&#8239;s (page-control
operations after 90&#8239;s) and the run resumes with a decline — the agent is told the
visitor didn't confirm, and carries on.

**When a tool is missing, check the console.** Definitions that fail validation (bad name,
oversized schema, over a cap) are dropped per-definition — the rest still register — and
the server echoes back what it accepted. The SDK logs a `console.warn` listing the
accepted names, so a silently absent action is one DevTools glance away.

## Guardrails

- **Registering is the opt-in.** Actions come from code you shipped, so there is no
  per-visitor consent gate (unlike
  [page control](/product/widget/#the-in-app-ai-assistant-page-control), which touches the
  DOM). The visitor-facing `confirm` card is on by default anyway.
- **Per-agent switch.** Agent → *Client actions* turns the whole surface off for an
  agent, and each `app_*` tool takes the same per-tool policies (auto / require approval /
  disabled) as every other tool.
- **Caps everywhere.** 20 actions per conversation, 10 action calls per run, a
  4&#8239;000-character schema budget per action and 16&#8239;000 characters of stored
  definitions total, schema validation before anything reaches the browser, and results
  size-capped again server-side.
- **Identity gating.** With `requiresIdentity`, anonymous visitors never even see the
  tool — it is withheld from the model, not refused at call time.
- **Full trace.** Every call, confirm, and result is an `AgentStep` on the run, labeled
  **App action** in the trace viewer.

## Recipes

**Refunds with a human in the loop** — the visitor asks, the agent prepares, *your team*
approves, the visitor confirms:

```js
Stept('action', {
  name: 'refund_last_order',
  description: "Refund the current user's most recent order, in full",
  approval: true,   // pauses for team approval before the visitor's confirm card
  run: () => api.refundLastOrder(),
});
```

**Prefill a form the user is asking about** — no confirm needed, nothing destructive:

```js
Stept('action', {
  name: 'prefill_invite_form',
  description: 'Open the invite dialog prefilled with an email address',
  params: { type: 'object', properties: { email: { type: 'string' } }, required: ['email'] },
  confirm: false,
  run: ({ email }) => openInviteDialog({ email }),
});
```

**Gated to signed-in users** — plan changes only for verified identities:

```js
Stept('action', {
  name: 'switch_to_annual_billing',
  description: 'Switch the current subscription to annual billing',
  confirm: true,
  requiresIdentity: true,
  run: () => billing.switchInterval('annual'),
});
```
