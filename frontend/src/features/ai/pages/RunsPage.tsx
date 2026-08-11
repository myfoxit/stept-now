import { Activity, ChevronRight } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { fullDateTime, timeAgo } from '@/lib/format'

import { RunStatusBadge } from '../components/status'
import { AiNav, ErrorState, ListSkeleton, PageHeader, PageShell, ScrollBody } from '../components/shell'
import { useRuns } from '../hooks'
import { t } from '@/i18n'

const STATUSES = [
  'queued',
  'running',
  'awaiting_approval',
  'completed',
  'failed',
  'handed_off',
  'canceled',
]

export function Component() {
  const navigate = useNavigate()
  const [status, setStatus] = useState('')
  const { data, isLoading, isError, refetch } = useRuns(status ? { status } : {})
  const runs = data?.items ?? []

  return (
    <PageShell>
      <PageHeader
        title={t('ai.agent_runs')}
        description={t('ai.every_agent_execution_with_its_full')}
        actions={
          <NativeSelect
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label={t('common.filter_by_status')}
            size="sm"
          >
            <NativeSelectOption value="">{t('common.all_statuses')}</NativeSelectOption>
            {STATUSES.map((s) => (
              <NativeSelectOption key={s} value={s}>
                {s.replace(/_/g, ' ')}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        }
      />
      <AiNav />
      <ScrollBody>
        {isLoading ? (
          <ListSkeleton />
        ) : isError ? (
          <ErrorState onRetry={() => refetch()} />
        ) : runs.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Activity />
              </EmptyMedia>
              <EmptyTitle>{t('ai.no_runs_yet')}</EmptyTitle>
              <EmptyDescription>
                {t('ai.when_an_agent_handles_a_conversation')}
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('common.agent')}</TableHead>
                  <TableHead>{t('common.status')}</TableHead>
                  <TableHead className="text-right">{t('common.tokens')}</TableHead>
                  <TableHead>{t('ai.started')}</TableHead>
                  <TableHead className="w-8" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((run) => (
                  <TableRow
                    key={run.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/ai/runs/${run.id}`)}
                  >
                    <TableCell className="font-medium">{run.agent_name ?? 'Agent'}</TableCell>
                    <TableCell>
                      <RunStatusBadge status={run.status} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {(run.input_tokens + run.output_tokens).toLocaleString()}
                    </TableCell>
                    <TableCell
                      className="text-muted-foreground"
                      title={run.started_at ? fullDateTime(run.started_at) : ''}
                    >
                      {run.started_at ? `${timeAgo(run.started_at)} ago` : '—'}
                    </TableCell>
                    <TableCell>
                      <ChevronRight className="size-4 text-muted-foreground" />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </ScrollBody>
    </PageShell>
  )
}

export default Component
