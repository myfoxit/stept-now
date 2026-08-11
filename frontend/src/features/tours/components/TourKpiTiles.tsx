import type { ReactNode } from 'react'

import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

import type { TourStats } from '../api'
import { compactNumber, formatRate } from '../lib'
import { t } from '@/i18n'

function StatTile({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: ReactNode
  hint?: string
  tone?: 'default' | 'warning'
}) {
  return (
    <Card className="gap-1 p-4">
      <div className="text-sm text-muted-foreground">{label}</div>
      {/* Proportional figures for large standalone values (dataviz). */}
      <div
        className={cn(
          'text-2xl font-semibold',
          tone === 'warning' && 'text-amber-700 dark:text-amber-400'
        )}
      >
        {value}
      </div>
      {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
    </Card>
  )
}

export function TourKpiTiles({ stats }: { stats: TourStats }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatTile
        label={t('common.starts')}
        value={compactNumber(stats.starts)}
        hint={`${compactNumber(stats.dismissals)} dismissed`}
      />
      <StatTile
        label={t('tours.unique_starts')}
        value={compactNumber(stats.unique_starts ?? 0)}
        hint="Distinct contacts"
      />
      <StatTile
        label={t('common.completion_rate')}
        value={formatRate(stats.completion_rate)}
        hint={`${compactNumber(stats.completions)} completed`}
      />
      <StatTile
        label={t('tours.step_errors')}
        value={compactNumber(stats.step_errors ?? 0)}
        tone={(stats.step_errors ?? 0) > 0 ? 'warning' : 'default'}
        hint="Steps the player could not find"
      />
    </div>
  )
}
