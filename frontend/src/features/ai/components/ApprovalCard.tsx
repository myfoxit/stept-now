/** A pending approval: shows the requested tool + input with approve/reject + note. */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Check, ExternalLink, Loader2, ShieldQuestion, X } from 'lucide-react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { timeAgo } from '@/lib/format'
import { currentWorkspaceId } from '@/stores/auth'

import { aiApi, aiKeys, type Approval } from '../api'
import { t } from '@/i18n'

export function ApprovalCard({ approval }: { approval: Approval }) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const [note, setNote] = useState('')

  const decide = useMutation({
    mutationFn: (approved: boolean) =>
      aiApi.decideApproval(approval.id, { approved, note: note.trim() || null }),
    onSuccess: (_data, approved) => {
      queryClient.setQueryData<Approval[]>(aiKeys.approvals(workspaceId, 'pending'), (prev) =>
        (prev ?? []).filter((a) => a.id !== approval.id)
      )
      toast.success(approved ? 'Approved' : 'Rejected')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not record decision'),
  })

  return (
    <Card>
      <CardHeader className="flex-row items-start gap-3 space-y-0">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400">
          <ShieldQuestion className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{approval.agent_name ?? 'Agent'}</span>
            <span className="text-sm text-muted-foreground">{t('ai.wants_to_run')}</span>
            <Badge variant="outline" className="font-mono text-xs">
              {approval.tool_key}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            Requested {timeAgo(approval.requested_at)} ago · expires {timeAgo(approval.expires_at)}
          </p>
        </div>
        <Button variant="ghost" size="sm" asChild>
          <Link to={`/inbox/${approval.conversation_id}`}>
            {t('ai.view')} <ExternalLink className="size-3.5" />
          </Link>
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {Object.keys(approval.tool_input ?? {}).length > 0 ? (
          <pre className="max-h-40 overflow-auto rounded-md bg-muted/60 p-2 text-xs">
            <code>{JSON.stringify(approval.tool_input, null, 2)}</code>
          </pre>
        ) : null}
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={t('ai.add_a_note_optional')}
            aria-label={t('ai.decision_note')}
            className="h-9"
          />
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => decide.mutate(false)}
              disabled={decide.isPending}
            >
              {decide.isPending ? <Loader2 className="size-4 animate-spin" /> : <X className="size-4" />}
              Reject
            </Button>
            <Button onClick={() => decide.mutate(true)} disabled={decide.isPending}>
              {decide.isPending ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
              Approve
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
