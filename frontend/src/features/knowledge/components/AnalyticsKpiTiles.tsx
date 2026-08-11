import type { ReactNode } from 'react'
import { ThumbsDown, ThumbsUp } from 'lucide-react'

import { Card } from '@/components/ui/card'

import type { SearchAnalyticsOverview } from '../api'
import { compactNumber, formatRate, formatScore } from '../lib'
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

export function AnalyticsKpiTiles({ overview }: { overview: SearchAnalyticsOverview }) {
  const { queries, feedback, ai } = overview
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
      <StatTile
        label={t('knowledge.total_queries')}
        value={compactNumber(queries.total)}
        hint={
          queries.avg_latency_ms == null
            ? undefined
            : `Avg latency ${Math.round(queries.avg_latency_ms)} ms`
        }
      />
      <StatTile
        label={t('knowledge.zero_result_rate')}
        value={formatRate(queries.zero_result_rate)}
        hint={`${compactNumber(queries.zero_result_count)} quer${
          queries.zero_result_count === 1 ? 'y' : 'ies'
        } found nothing`}
      />
      <StatTile label={t('knowledge.avg_top_score')} value={formatScore(queries.avg_top_score)} />
      <StatTile
        label={t('knowledge.answer_feedback')}
        value={
          <span className="flex items-center gap-3 tabular-nums">
            <span className="flex items-center gap-1">
              <ThumbsUp className="size-4 text-muted-foreground" aria-label={t('knowledge.helpful')} />
              {compactNumber(feedback.up)}
            </span>
            <span className="flex items-center gap-1">
              <ThumbsDown className="size-4 text-muted-foreground" aria-label={t('knowledge.not_helpful')} />
              {compactNumber(feedback.down)}
            </span>
          </span>
        }
        hint={`Negative rate ${formatRate(feedback.negative_rate)}`}
      />
      <StatTile
        label={t('knowledge.ai_deflection_rate')}
        value={formatRate(ai.deflection_rate)}
        hint={`${compactNumber(ai.completed)} of ${compactNumber(ai.runs)} runs, ${compactNumber(
          ai.handed_off
        )} handed off`}
      />
    </div>
  )
}
