import { Map } from 'lucide-react'

import { PlaceholderPage } from '@/components/layout/PlaceholderPage'

// Placeholder — implemented by its frontend wave agent (see docs/CONTRACTS.md).
export function Component() {
  return (
    <PlaceholderPage
      title="Tour editor"
      description="This area is being built — it arrives in an upcoming wave."
      icon={Map}
    />
  )
}

export default Component
