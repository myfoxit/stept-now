# @stept/react

React bindings for the [Stept](https://github.com/myfoxit/stept-now) messenger +
AI assistant.

```tsx
import { SteptProvider, useSteptAction } from '@stept/react'

function App() {
  return (
    <SteptProvider settings={{ workspaceKey: 'wk_…', apiBase: 'https://stept.your-domain.com' }}>
      <Billing />
    </SteptProvider>
  )
}

function Billing() {
  useSteptAction({
    name: 'apply_promo_code',
    description: 'Apply a promo code to the current subscription',
    params: { type: 'object', properties: { code: { type: 'string' } }, required: ['code'] },
    confirm: true,
    run: ({ code }) => billing.applyPromo(String(code)),
  })
  return <PricingTable />
}
```

The action exists only while the component is mounted, re-registers when your
declared deps change, and executes in the browser with the signed-in user's
session. Full docs: the **Actions SDK** page on your Stept docs site.
