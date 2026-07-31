import type { LucideIcon } from 'lucide-react'

import { Card, CardContent } from '@/components/ui/card'

/** Temporary stand-in rendered by pre-registered routes until their wave lands. */
export function PlaceholderPage({
  title,
  description,
  icon: Icon,
}: {
  title: string
  description: string
  icon?: LucideIcon
}) {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <Card className="w-full max-w-md">
        <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
          {Icon ? <Icon className="size-10 text-muted-foreground/50" /> : null}
          <h1 className="text-lg font-semibold">{title}</h1>
          <p className="text-sm text-muted-foreground">{description}</p>
        </CardContent>
      </Card>
    </div>
  )
}
