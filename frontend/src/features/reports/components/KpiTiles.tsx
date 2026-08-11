import type { ReactNode } from 'react'

import { Card } from '@/components/ui/card'

import type { ReportTotals } from '../api'
import { compactNumber, formatMinutes, formatRate } from '../lib'
import { t } from '@/i18n'

function StatTile({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <Card className="gap-1 p-4">
      <div className="text-sm text-muted-foreground">{label}</div>
      {/* Proportional figures for large standalone values (dataviz). */}
      <div className="text-2xl font-semibold">{value}</div>
      {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
    </Card>
  )
}

export function KpiTiles({ totals }: { totals: ReportTotals }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
      <StatTile label={t('reports.new_conversations')} value={compactNumber(totals.new_conversations)} />
      <StatTile label={t('reports.resolved')} value={compactNumber(totals.resolved_conversations)} />
      <StatTile label={t('reports.resolution_rate')} value={formatRate(totals.resolution_rate)} />
      <StatTile
        label={t('reports.median_first_response')}
        value={formatMinutes(totals.median_first_response_minutes)}
        hint={`Median resolution ${formatMinutes(totals.median_resolution_minutes)}`}
      />
      <StatTile
        label="CSAT"
        value={totals.csat_avg == null ? '—' : `${totals.csat_avg.toFixed(1)} / 5`}
        hint={`${compactNumber(totals.csat_count)} rating${totals.csat_count === 1 ? '' : 's'}`}
      />
      <StatTile
        label={t('reports.ai_resolution_rate')}
        value={formatRate(totals.ai_resolution_rate)}
        hint={`${compactNumber(totals.ai_resolved)} of ${compactNumber(totals.ai_runs)} AI runs`}
      />
    </div>
  )
}
