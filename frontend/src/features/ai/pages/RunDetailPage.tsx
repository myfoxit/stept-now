import { ArrowLeft, ExternalLink } from 'lucide-react'
import { Link, useParams } from 'react-router'

import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { fullDateTime } from '@/lib/format'

import { RunStatusBadge } from '../components/status'
import { ErrorState, PageHeader, PageShell, ScrollBody } from '../components/shell'
import { StepTrace } from '../components/StepTrace'
import { useRun } from '../hooks'

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-sm font-medium tabular-nums">{value}</p>
    </div>
  )
}

export function Component() {
  const { runId = '' } = useParams()
  const { data, isLoading, isError, refetch } = useRun(runId)

  return (
    <PageShell>
      <PageHeader
        title={data ? `${data.run.agent_name ?? 'Agent'} run` : 'Run trace'}
        description="Full step-by-step execution trace"
        back={
          <Button variant="ghost" size="icon" asChild aria-label="Back to runs">
            <Link to="/ai/runs">
              <ArrowLeft className="size-4" />
            </Link>
          </Button>
        }
        actions={
          data ? (
            <Button variant="outline" size="sm" asChild>
              <Link to={`/inbox/${data.run.conversation_id}`}>
                Conversation <ExternalLink className="size-3.5" />
              </Link>
            </Button>
          ) : null
        }
      />
      <ScrollBody className="space-y-4">
        {isLoading ? (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        ) : isError || !data ? (
          <ErrorState onRetry={() => refetch()} />
        ) : (
          <>
            <Card>
              <CardContent className="flex flex-wrap items-center gap-6">
                <div>
                  <p className="text-xs text-muted-foreground">Status</p>
                  <RunStatusBadge status={data.run.status} />
                </div>
                <Stat label="Input tokens" value={data.run.input_tokens.toLocaleString()} />
                <Stat label="Output tokens" value={data.run.output_tokens.toLocaleString()} />
                <Stat
                  label="Started"
                  value={data.run.started_at ? fullDateTime(data.run.started_at) : '—'}
                />
                <Stat
                  label="Finished"
                  value={data.run.finished_at ? fullDateTime(data.run.finished_at) : '—'}
                />
              </CardContent>
            </Card>

            {data.run.error ? (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
                {data.run.error}
              </div>
            ) : null}

            <Card>
              <CardContent>
                <p className="mb-3 text-sm font-medium">Trace ({data.steps.length} steps)</p>
                <StepTrace steps={data.steps} />
              </CardContent>
            </Card>
          </>
        )}
      </ScrollBody>
    </PageShell>
  )
}

export default Component
