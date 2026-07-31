import { BookOpen } from 'lucide-react'

import { PlaceholderPage } from '@/components/layout/PlaceholderPage'

// Placeholder — implemented by its frontend wave agent (see docs/CONTRACTS.md).
export function Component() {
  return (
    <PlaceholderPage
      title="Help center articles"
      description="This area is being built — it arrives in an upcoming wave."
      icon={BookOpen}
    />
  )
}

export default Component
