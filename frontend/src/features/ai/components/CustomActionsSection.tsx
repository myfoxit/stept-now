/** Workspace custom actions: list, create, edit, delete. */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Pencil, Plus, Trash2, Zap } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { currentWorkspaceId, useHasPerm } from '@/stores/auth'

import { aiApi, aiKeys, type CustomAction } from '../api'
import { useActions } from '../hooks'
import { ActionDialog } from './ActionDialog'

export function CustomActionsSection() {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const canManage = useHasPerm('ai:manage')
  const actions = useActions()
  const [dialog, setDialog] = useState<{ open: boolean; edit?: CustomAction | null }>({ open: false })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => aiApi.deleteAction(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: aiKeys.actions(workspaceId) })
      toast.success('Action deleted')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Delete failed'),
  })

  const items = actions.data ?? []

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">Custom actions</p>
          <p className="text-xs text-muted-foreground">HTTP tools the agent can call</p>
        </div>
        {canManage ? (
          <Button size="sm" variant="outline" onClick={() => setDialog({ open: true, edit: null })}>
            <Plus className="size-4" /> New action
          </Button>
        ) : null}
      </div>

      {actions.isLoading ? (
        <Spinner className="size-4" />
      ) : items.length === 0 ? (
        <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
          No custom actions yet. Add one to let the agent hit your APIs.
        </p>
      ) : (
        <ul className="space-y-2">
          {items.map((action) => (
            <li key={action.id} className="flex items-center gap-2 rounded-md border p-2.5">
              <Zap className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{action.name}</p>
                <p className="truncate text-xs text-muted-foreground">
                  <Badge variant="outline" className="mr-1 text-[10px]">
                    {action.method}
                  </Badge>
                  {action.url}
                </p>
              </div>
              {canManage ? (
                <>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-8"
                    aria-label={`Edit ${action.name}`}
                    onClick={() => setDialog({ open: true, edit: action })}
                  >
                    <Pencil className="size-4" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="size-8 text-destructive"
                    aria-label={`Delete ${action.name}`}
                    onClick={() => deleteMutation.mutate(action.id)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      <ActionDialog
        action={dialog.edit}
        open={dialog.open}
        onOpenChange={(open) => setDialog((prev) => ({ ...prev, open }))}
      />
    </div>
  )
}
