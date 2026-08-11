import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { useHasPerm } from '@/stores/auth'

import { ByAgentTable } from '../components/ByAgentTable'
import { ByChannelChart } from '../components/ByChannelChart'
import { ByDayChart } from '../components/ByDayChart'
import { KpiTiles } from '../components/KpiTiles'
import { BreakdownTable } from '@/features/reports/components/BreakdownTable'
import { SlaAttainmentTable } from '@/features/reports/components/SlaAttainmentTable'
import { useReportOverview } from '../hooks'
import { DAY_RANGES } from '../lib'
import { t } from '@/i18n'

export function Component() {
  const canRead = useHasPerm('reports:read')
  const [days, setDays] = useState(7)
  const overview = useReportOverview(days)

  if (!canRead) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card className="p-6 text-center text-sm text-muted-foreground">
          {t('reports.you_don_t_have_access_to')}
        </Card>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-4 border-b px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold">{t('reports.reports')}</h1>
          <p className="text-sm text-muted-foreground">
            {t('reports.volume_responsiveness_satisfaction_and_ai_performance')}
          </p>
        </div>
        <NativeSelect
          aria-label={t('common.date_range')}
          className="w-40"
          value={String(days)}
          onChange={(e) => setDays(Number(e.target.value))}
        >
          {DAY_RANGES.map((range) => (
            <NativeSelectOption key={range.value} value={range.value}>
              {range.label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto grid max-w-5xl gap-4">
          {overview.isLoading ? (
            <>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
                {[0, 1, 2, 3, 4, 5].map((i) => (
                  <Skeleton key={i} className="h-24 w-full" />
                ))}
              </div>
              <Skeleton className="h-64 w-full" />
              <div className="grid gap-4 lg:grid-cols-2">
                <Skeleton className="h-64 w-full" />
                <Skeleton className="h-64 w-full" />
              </div>
            </>
          ) : overview.isError ? (
            <Card className="p-6 text-center text-sm text-muted-foreground">
              Could not load reports.{' '}
              <Button variant="link" className="px-1" onClick={() => overview.refetch()}>
                {t('common.retry')}
              </Button>
            </Card>
          ) : overview.data ? (
            <>
              <KpiTiles totals={overview.data.totals} />
              <ByDayChart data={overview.data.by_day} />
              <div className="grid gap-4 lg:grid-cols-2">
                <ByChannelChart data={overview.data.by_channel} />
                <ByAgentTable data={overview.data.by_agent} />
              </div>
              <BreakdownTable days={days} />
              <SlaAttainmentTable days={days} />
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export default Component
