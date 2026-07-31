import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const STYLES: Record<string, string> = {
  live: 'border-transparent bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
  paused: 'border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-400',
  draft: '',
}

export function SurveyStatusBadge({ status }: { status: string }) {
  return (
    <Badge variant={status === 'draft' ? 'outline' : 'secondary'} className={cn(STYLES[status])}>
      {status}
    </Badge>
  )
}
