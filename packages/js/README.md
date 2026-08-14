# @stept/js

Typed, SSR-safe wrapper around the [Stept](https://github.com/myfoxit/stept-now)
widget loader: embed the messenger + AI assistant, and teach the assistant your
app's actions in one line.

> **Not yet on npm.** This package lives in the repo and the publish is imminent;
> until then, install it from the repo or use the script-tag path — see the
> [Actions SDK docs](https://docs.stepped.ai/product/actions-sdk/).

```ts
import { loadStept, registerAction } from '@stept/js'

loadStept({ workspaceKey: 'wk_…', apiBase: 'https://stept.your-domain.com' })

registerAction({
  name: 'invite_teammate',
  description: 'Invite a teammate to the current workspace by email',
  params: {
    type: 'object',
    properties: { email: { type: 'string' } },
    required: ['email'],
  },
  confirm: true, // in-chat "Run this?" card before executing
  run: async ({ email }) => inviteTeammate(email), // runs with the user's session
})
```

Every call queues until the loader script arrives, so order never matters. All
functions are no-ops on the server. Using React? See `@stept/react`.

Full docs: [docs.stepped.ai/product/actions-sdk](https://docs.stepped.ai/product/actions-sdk/).
