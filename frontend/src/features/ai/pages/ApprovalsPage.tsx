import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, Loader2, Plug, ShieldCheck, X } from 'lucide-react'
import { useCallback } from 'react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { useRealtime } from '@/api/ws'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { timeAgo } from '@/lib/format'
import { currentWorkspaceId } from '@/stores/auth'

import { aiApi, aiKeys, type Approval, type McpApproval } from '../api'
import { ApprovalCard } from '../components/ApprovalCard'
import { AiNav, ErrorState, ListSkeleton, PageHeader, PageShell, ScrollBody } from '../components/shell'
import { useApprovals, useMcpApprovals } from '../hooks'

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

/** A write tool called from an external MCP client, waiting for a decision. */
function McpApprovalCard({ approval }: { approval: McpApproval }) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()

  const decide = useMutation({
    mutationFn: (decision: 'approve' | 'deny') => aiApi.decideMcpApproval(approval.id, decision),
    onSuccess: (_data, decision) => {
      queryClient.setQueryData<McpApproval[]>(
        aiKeys.mcpApprovals(workspaceId, 'pending'),
        (prev) => (prev ?? []).filter((a) => a.id !== approval.id)
      )
      void queryClient.invalidateQueries({ queryKey: aiKeys.mcpApprovals(workspaceId, 'pending') })
      toast.success(decision === 'approve' ? 'Approved' : 'Denied')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not record decision'),
  })

  return (
    <Card>
      <CardHeader className="flex-row items-start gap-3 space-y-0">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-brand/10 text-brand">
          <Plug className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{approval.agent_name ?? 'Agent'}</span>
            <span className="text-sm text-muted-foreground">was asked to run</span>
            <Badge variant="outline" className="font-mono text-xs">
              {approval.tool_key}
            </Badge>
            <Badge variant="secondary" className="text-[10px]">
              MCP
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            Requested {timeAgo(approval.requested_at)} ago · expires {timeAgo(approval.expires_at)}
          </p>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {Object.keys(approval.tool_input ?? {}).length > 0 ? (
          <pre className="max-h-40 overflow-auto rounded-md bg-muted/60 p-2 text-xs">
            <code>{JSON.stringify(approval.tool_input, null, 2)}</code>
          </pre>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => decide.mutate('deny')} disabled={decide.isPending}>
            {decide.isPending ? <Loader2 className="size-4 animate-spin" /> : <X className="size-4" />}
            Deny
          </Button>
          <Button onClick={() => decide.mutate('approve')} disabled={decide.isPending}>
            {decide.isPending ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
            Approve
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

export function Component() {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  const key = aiKeys.approvals(workspaceId, 'pending')
  const { data: approvals, isLoading, isError, refetch } = useApprovals('pending')
  const mcp = useMcpApprovals('pending')

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
      <ScrollBody className="space-y-6">
        {isLoading || mcp.isLoading ? (
          <ListSkeleton />
        ) : isError ? (
          <ErrorState onRetry={() => refetch()} />
        ) : (!approvals || approvals.length === 0) && (mcp.data ?? []).length === 0 ? (
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
          <>
            {approvals && approvals.length > 0 ? (
              <div className="grid gap-4">
                {approvals.map((approval) => (
                  <ApprovalCard key={approval.id} approval={approval} />
                ))}
              </div>
            ) : null}
            {(mcp.data ?? []).length > 0 ? (
              <section className="space-y-3" aria-label="MCP approvals">
                <div>
                  <h2 className="text-sm font-semibold">MCP clients</h2>
                  <p className="text-xs text-muted-foreground">
                    Write tools called from Claude, Cursor or ChatGPT that are waiting for a
                    decision.
                  </p>
                </div>
                <div className="grid gap-4">
                  {(mcp.data ?? []).map((approval) => (
                    <McpApprovalCard key={approval.id} approval={approval} />
                  ))}
                </div>
              </section>
            ) : null}
          </>
        )}
      </ScrollBody>
    </PageShell>
  )
}

export default Component
