import { BarChart3 } from 'lucide-react'
import { useState } from 'react'

import { Card } from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { useHasPerm } from '@/stores/auth'

import { AnalyticsKpiTiles } from '../components/AnalyticsKpiTiles'
import { ContentGapsTable, TopQueriesTable } from '../components/AnalyticsTables'
import { QueriesBySourceChart } from '../components/QueriesBySourceChart'
import { QueryVolumeChart } from '../components/QueryVolumeChart'
import { ErrorState, KnowledgeNav, PageHeader, PageShell, ScrollBody } from '../components/shell'
import { useKnowledgeAnalytics } from '../hooks'
import { DAY_RANGES } from '../lib'
import { t } from '@/i18n'

export function Component() {
  const canRead = useHasPerm('reports:read')
  const [days, setDays] = useState(7)
  const overview = useKnowledgeAnalytics(days)

  return (
    <PageShell>
      <PageHeader
        title={t('knowledge.search_analytics')}
        description={t('knowledge.how_well_your_knowledge_base_answers')}
        actions={
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
        }
      />
      <KnowledgeNav />
      <ScrollBody>
        <div className="mx-auto grid max-w-5xl gap-4">
          {!canRead ? (
            <Card className="p-6 text-center text-sm text-muted-foreground">
              {t('knowledge.you_don_t_have_access_to')}
            </Card>
          ) : overview.isLoading ? (
            <>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                {[0, 1, 2, 3, 4].map((i) => (
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
            <ErrorState onRetry={() => overview.refetch()} />
          ) : overview.data ? (
            overview.data.queries.total === 0 ? (
              <Empty className="border">
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <BarChart3 />
                  </EmptyMedia>
                  <EmptyTitle>{t('knowledge.no_search_activity_yet')}</EmptyTitle>
                  <EmptyDescription>
                    {t('knowledge.once_contacts_and_ai_agents_start')}
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <>
                <AnalyticsKpiTiles overview={overview.data} />
                <QueryVolumeChart data={overview.data.queries.per_day} />
                <div className="grid gap-4 lg:grid-cols-2">
                  <QueriesBySourceChart data={overview.data.queries.by_source} />
                  <TopQueriesTable data={overview.data.top_queries} />
                </div>
                <ContentGapsTable data={overview.data.zero_result_queries} />
              </>
            )
          ) : null}
        </div>
      </ScrollBody>
    </PageShell>
  )
}

export default Component
