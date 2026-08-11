import { Pencil, Plus, Trash2, Wand2 } from 'lucide-react'
import { useState } from 'react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuthStore, useHasPerm } from '@/stores/auth'

import type { Macro } from '../api'
import { MACRO_ACTION_SPECS } from '../constants'
import { useDeleteMacro, useMacros } from '../hooks'
import { MacroEditorDialog } from './MacroEditorDialog'
import { t } from '@/i18n'

function actionSummary(macro: Macro): string {
  const labels = (macro.actions ?? []).map((a) => {
    const type = (a as Record<string, unknown>).type
    return MACRO_ACTION_SPECS[String(type)]?.label ?? String(type)
  })
  return labels.length === 0 ? 'No actions' : labels.join(' → ')
}

export function MacrosTab() {
  const canWrite = useHasPerm('conversations:write')
  const canManage = useHasPerm('conversations:manage')
  const myId = useAuthStore((s) => s.user?.id)
  const macros = useMacros()
  const remove = useDeleteMacro()

  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<Macro | null>(null)
  const [deleting, setDeleting] = useState<Macro | null>(null)

  function openNew() {
    setEditing(null)
    setEditorOpen(true)
  }
  function openEdit(macro: Macro) {
    setEditing(macro)
    setEditorOpen(true)
  }

  /** Personal macros are editable by their creator; global ones need manage. */
  function canModify(macro: Macro): boolean {
    if (!canWrite) return false
    if (macro.visibility === 'global') return canManage
    return macro.created_by === myId || canManage
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {t('automation.one_click_action_bundles_agents_run')}
        </p>
        {canWrite ? (
          <Button size="sm" onClick={openNew}>
            <Plus className="size-4" /> {t('automation.new_macro')}
          </Button>
        ) : null}
      </div>

      {macros.isLoading ? (
        <div className="grid gap-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : macros.isError ? (
        <Card className="p-6 text-center text-sm text-muted-foreground">
          Could not load macros.{' '}
          <Button variant="link" className="px-1" onClick={() => macros.refetch()}>
            {t('common.retry')}
          </Button>
        </Card>
      ) : !macros.data || macros.data.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Wand2 />
            </EmptyMedia>
            <EmptyTitle>{t('common.no_macros_yet')}</EmptyTitle>
            <EmptyDescription>
              {t('automation.bundle_assignment_tags_status_and_replies')}
            </EmptyDescription>
          </EmptyHeader>
          {canWrite ? (
            <EmptyContent>
              <Button onClick={openNew}>
                <Plus className="size-4" /> {t('automation.create_your_first_macro')}
              </Button>
            </EmptyContent>
          ) : null}
        </Empty>
      ) : (
        <ul className="grid gap-3">
          {macros.data.map((macro) => (
            <li key={macro.id}>
              <Card className="flex flex-row items-center gap-4 p-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{macro.name}</span>
                    <Badge variant={macro.visibility === 'global' ? 'default' : 'secondary'}>
                      {macro.visibility === 'global' ? 'Global' : 'Personal'}
                    </Badge>
                  </div>
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {actionSummary(macro)}
                  </p>
                </div>
                {canModify(macro) ? (
                  <>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Edit ${macro.name}`}
                      onClick={() => openEdit(macro)}
                    >
                      <Pencil className="size-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${macro.name}`}
                      onClick={() => setDeleting(macro)}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </>
                ) : null}
              </Card>
            </li>
          ))}
        </ul>
      )}

      <MacroEditorDialog open={editorOpen} onOpenChange={setEditorOpen} macro={editing} />

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              {t('automation.the_macro_will_disappear_for_everyone')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleting) remove.mutate(deleting.id)
                setDeleting(null)
              }}
            >
              {t('common.delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
