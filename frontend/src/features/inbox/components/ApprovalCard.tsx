/** Inline pending-approval card shown in the context pane. */

import { useState } from 'react'
import { ShieldAlert } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useDecideApproval } from '@/features/inbox/hooks'
import type { Approval } from '@/features/inbox/api'

export function ApprovalCard({ approval, canApprove }: { approval: Approval; canApprove: boolean }) {
  const [note, setNote] = useState('')
  const decide = useDecideApproval()

  return (
    <div className="rounded-lg border border-amber-400/50 bg-amber-50 p-3 dark:bg-amber-950/30">
      <div className="flex items-center gap-2 text-sm font-medium text-amber-900 dark:text-amber-200">
        <ShieldAlert className="size-4" />
        Approval needed
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{approval.agent_name ?? 'AI agent'}</span> wants
        to run <code className="rounded bg-background px-1">{approval.tool_key}</code>
      </p>
      {Object.keys(approval.tool_input ?? {}).length ? (
        <pre className="mt-2 max-h-24 overflow-auto rounded bg-background/70 p-2 text-[11px]">
          {JSON.stringify(approval.tool_input, null, 2)}
        </pre>
      ) : null}
      {canApprove ? (
        <>
          <Input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Note (optional)"
            aria-label="Approval note"
            className="mt-2 h-8"
          />
          <div className="mt-2 flex gap-2">
            <Button
              size="sm"
              className="flex-1"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ id: approval.id, approved: true, note: note || undefined })}
            >
              Approve
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="flex-1"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ id: approval.id, approved: false, note: note || undefined })}
            >
              Reject
            </Button>
          </div>
        </>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">You don&apos;t have permission to decide.</p>
      )}
    </div>
  )
}
