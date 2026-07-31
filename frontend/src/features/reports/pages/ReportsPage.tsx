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
import { useReportOverview } from '../hooks'
import { DAY_RANGES } from '../lib'

export function Component() {
  const canRead = useHasPerm('reports:read')
  const [days, setDays] = useState(7)
  const overview = useReportOverview(days)

  if (!canRead) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card className="p-6 text-center text-sm text-muted-foreground">
          You don’t have access to reports.
        </Card>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-4 border-b px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold">Reports</h1>
          <p className="text-sm text-muted-foreground">
            Volume, responsiveness, satisfaction and AI performance.
          </p>
        </div>
        <NativeSelect
          aria-label="Date range"
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
                Retry
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
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export default Component
