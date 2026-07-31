import { Activity, ArrowRight, Bot, Cpu, ShieldQuestion } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Link } from 'react-router'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'

import { AiNav, PageHeader, PageShell, ScrollBody } from '../components/shell'
import { AgentStatusBadge } from '../components/status'
import { useAgents, useApprovals, useProviders, useRuns } from '../hooks'

function StatCard({
  to,
  icon: Icon,
  label,
  value,
  hint,
  highlight,
}: {
  to: string
  icon: LucideIcon
  label: string
  value: string | number
  hint?: string
  highlight?: boolean
}) {
  return (
    <Link to={to}>
      <Card className={highlight ? 'border-amber-500/40' : 'transition-colors hover:border-primary/40'}>
        <CardContent className="flex items-center gap-4">
          <div
            className={`flex size-10 items-center justify-center rounded-md ${
              highlight ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400' : 'bg-muted'
            }`}
          >
            <Icon className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-2xl font-semibold tabular-nums">{value}</p>
            <p className="text-sm text-muted-foreground">{label}</p>
          </div>
          {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
          <ArrowRight className="size-4 text-muted-foreground" />
        </CardContent>
      </Card>
    </Link>
  )
}

export function Component() {
  const agents = useAgents()
  const providers = useProviders()
  const approvals = useApprovals('pending')
  const runs = useRuns({})

  const liveAgents = (agents.data ?? []).filter((a) => a.status === 'live').length
  const pending = approvals.data?.length ?? 0

  return (
    <PageShell>
      <PageHeader title="AI agents" description="Your AI support automation at a glance" />
      <AiNav />
      <ScrollBody className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            to="/ai/agents"
            icon={Bot}
            label="Agents"
            value={agents.data?.length ?? 0}
            hint={`${liveAgents} live`}
          />
          <StatCard
            to="/ai/providers"
            icon={Cpu}
            label="Providers"
            value={providers.data?.length ?? 0}
          />
          <StatCard
            to="/ai/approvals"
            icon={ShieldQuestion}
            label="Pending approvals"
            value={pending}
            highlight={pending > 0}
          />
          <StatCard to="/ai/runs" icon={Activity} label="Recent runs" value={runs.data?.total ?? 0} />
        </div>

        <div>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium">Your agents</h2>
            <Link to="/ai/agents" className="text-sm text-primary hover:underline">
              View all
            </Link>
          </div>
          {(agents.data ?? []).length === 0 ? (
            <Card>
              <CardContent className="py-8 text-center text-sm text-muted-foreground">
                No agents yet.{' '}
                <Link to="/ai/agents" className="text-primary hover:underline">
                  Create one
                </Link>{' '}
                to get started.
              </CardContent>
            </Card>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {(agents.data ?? []).slice(0, 6).map((agent) => (
                <Link key={agent.id} to={`/ai/agents/${agent.id}`}>
                  <Card className="transition-colors hover:border-primary/40">
                    <CardContent className="flex items-center gap-3">
                      <div className="flex size-9 items-center justify-center rounded-md bg-muted text-lg">
                        {agent.avatar_emoji || '🤖'}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium">{agent.name}</p>
                        <Badge variant="outline" className="mt-0.5 text-[10px]">
                          {agent.model_ref ? agent.model_ref.split(':')[1] : 'default model'}
                        </Badge>
                      </div>
                      <AgentStatusBadge status={agent.status} />
                    </CardContent>
                  </Card>
                </Link>
              ))}
            </div>
          )}
        </div>
      </ScrollBody>
    </PageShell>
  )
}

export default Component
