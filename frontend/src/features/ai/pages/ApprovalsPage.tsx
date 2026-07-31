import { useQueryClient } from '@tanstack/react-query'
import { ShieldCheck } from 'lucide-react'
import { useCallback } from 'react'

import { useRealtime } from '@/api/ws'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { currentWorkspaceId } from '@/stores/auth'

import { aiKeys, type Approval } from '../api'
import { ApprovalCard } from '../components/ApprovalCard'
import { AiNav, ErrorState, ListSkeleton, PageHeader, PageShell, ScrollBody } from '../components/shell'
import { useApprovals } from '../hooks'

/** Best-effort ApprovalOut built from an approval.pending broadcast payload. */
function fromBroadcast(data: Record<string, unknown>): Approval {
  const now = new Date().toISOString()
  const expires = new Date(Date.now() + 24 * 3600 * 1000).toISOString()
  return {
    id: String(data.approval_id ?? data.id ?? ''),
    run_id: String(data.run_id ?? ''),
    conversation_id: String(data.conversation_id ?? ''),
    agent_id: String(data.agent_id ?? ''),
    agent_name: (data.agent_name ?? data.agent ?? null) as string | null,
    tool_key: String(data.tool_key ?? 'tool'),
    tool_input: (data.tool_input as Record<string, never>) ?? {},
    status: 'pending',
    requested_at: now,
    expires_at: expires,
    decided_by: null,
    decided_at: null,
    note: null,
    created_at: now,
  }
}

export function Component() {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  const key = aiKeys.approvals(workspaceId, 'pending')
  const { data: approvals, isLoading, isError, refetch } = useApprovals('pending')

  const onPending = useCallback(
    (data: Record<string, unknown>) => {
      const incoming = fromBroadcast(data)
      if (!incoming.id) return
      queryClient.setQueryData<Approval[]>(key, (prev) => {
        const existing = prev ?? []
        if (existing.some((a) => a.id === incoming.id)) return existing
        return [incoming, ...existing]
      })
    },
    [queryClient, key]
  )

  const onDecided = useCallback(
    (data: Record<string, unknown>) => {
      const id = String(data.approval_id ?? data.id ?? '')
      if (!id) return
      queryClient.setQueryData<Approval[]>(key, (prev) =>
        (prev ?? []).filter((a) => a.id !== id)
      )
    },
    [queryClient, key]
  )

  useRealtime('approval.pending', onPending)
  useRealtime('approval.decided', onDecided)

  return (
    <PageShell>
      <PageHeader
        title="Approvals"
        description="Review actions your agents want to take before they run"
      />
      <AiNav />
      <ScrollBody className="space-y-4">
        {isLoading ? (
          <ListSkeleton />
        ) : isError ? (
          <ErrorState onRetry={() => refetch()} />
        ) : !approvals || approvals.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <ShieldCheck />
              </EmptyMedia>
              <EmptyTitle>All caught up</EmptyTitle>
              <EmptyDescription>
                There are no pending approvals. Requests appear here in real time when an agent needs
                sign-off.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="grid gap-4">
            {approvals.map((approval) => (
              <ApprovalCard key={approval.id} approval={approval} />
            ))}
          </div>
        )}
      </ScrollBody>
    </PageShell>
  )
}

export default Component
