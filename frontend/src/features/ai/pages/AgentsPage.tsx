import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bot, Plus } from 'lucide-react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { currentWorkspaceId, useHasPerm } from '@/stores/auth'

import { aiApi, aiKeys } from '../api'
import { AgentStatusBadge } from '../components/status'
import { AiNav, ErrorState, ListSkeleton, PageHeader, PageShell, ScrollBody } from '../components/shell'
import { useAgents } from '../hooks'

export function Component() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  const canManage = useHasPerm('ai:manage')
  const { data: agents, isLoading, isError, refetch } = useAgents()

  const createMutation = useMutation({
    mutationFn: () =>
      aiApi.createAgent({
        name: 'New agent',
        avatar_emoji: '🤖',
        status: 'draft',
      }),
    onSuccess: (agent) => {
      queryClient.invalidateQueries({ queryKey: aiKeys.agents(workspaceId) })
      navigate(`/ai/agents/${agent.id}`)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not create agent'),
  })

  return (
    <PageShell>
      <PageHeader
        title="AI agents"
        description="Autonomous agents that resolve conversations for you"
        actions={
          canManage ? (
            <Button onClick={() => createMutation.mutate()} disabled={createMutation.isPending}>
              <Plus className="size-4" /> New agent
            </Button>
          ) : null
        }
      />
      <AiNav />
      <ScrollBody>
        {isLoading ? (
          <ListSkeleton />
        ) : isError ? (
          <ErrorState onRetry={() => refetch()} />
        ) : !agents || agents.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Bot />
              </EmptyMedia>
              <EmptyTitle>No agents yet</EmptyTitle>
              <EmptyDescription>
                Build an AI agent that answers questions, cites your knowledge base and hands off to
                your team when needed.
              </EmptyDescription>
            </EmptyHeader>
            {canManage ? (
              <EmptyContent>
                <Button onClick={() => createMutation.mutate()} disabled={createMutation.isPending}>
                  <Plus className="size-4" /> Create your first agent
                </Button>
              </EmptyContent>
            ) : null}
          </Empty>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {agents.map((agent) => (
              <Card
                key={agent.id}
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/ai/agents/${agent.id}`)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') navigate(`/ai/agents/${agent.id}`)
                }}
                className="cursor-pointer transition-colors hover:border-primary/40"
              >
                <CardContent className="flex flex-col gap-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex size-10 items-center justify-center rounded-md bg-muted text-xl">
                      {agent.avatar_emoji || '🤖'}
                    </div>
                    <AgentStatusBadge status={agent.status} />
                  </div>
                  <div className="min-w-0">
                    <p className="truncate font-medium">{agent.name}</p>
                    <p className="line-clamp-2 text-sm text-muted-foreground">
                      {agent.description || 'No description'}
                    </p>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </ScrollBody>
    </PageShell>
  )
}

export default Component
