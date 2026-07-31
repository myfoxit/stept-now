import { Link } from 'react-router'

import { Button } from '@/components/ui/button'

export function Component() {
  return (
    <div className="flex h-svh flex-col items-center justify-center gap-4 p-8 text-center">
      <p className="text-6xl font-bold text-muted-foreground/40">404</p>
      <h1 className="text-xl font-semibold">This page doesn't exist</h1>
      <Button asChild>
        <Link to="/">Back to the inbox</Link>
      </Button>
    </div>
  )
}

export default Component
