import { ArrowDown, ArrowUp, Pencil, Plus, Trash2, Workflow } from 'lucide-react'
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
import { Switch } from '@/components/ui/switch'
import { useHasPerm } from '@/stores/auth'

import type { AutomationRule } from '../api'
import { AUTOMATION_EVENTS } from '../constants'
import { useAutomationRules, useDeleteRule, useReorderRules, useToggleRule } from '../hooks'
import { describeCondition } from '../lib'
import { RuleEditorDialog } from './RuleEditorDialog'

function eventLabel(event: string): string {
  return AUTOMATION_EVENTS.find((e) => e.value === event)?.label ?? event
}

export function RulesTab() {
  const canManage = useHasPerm('automations:manage')
  const rules = useAutomationRules()
  const toggle = useToggleRule()
  const reorder = useReorderRules()
  const remove = useDeleteRule()

  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<AutomationRule | null>(null)
  const [deleting, setDeleting] = useState<AutomationRule | null>(null)

  function openNew() {
    setEditing(null)
    setEditorOpen(true)
  }
  function openEdit(rule: AutomationRule) {
    setEditing(rule)
    setEditorOpen(true)
  }

  function move(index: number, direction: -1 | 1) {
    const list = rules.data
    if (!list) return
    const target = index + direction
    if (target < 0 || target >= list.length) return
    const ids = list.map((r) => r.id)
    ;[ids[index], ids[target]] = [ids[target]!, ids[index]!]
    reorder.mutate(ids)
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Rules run top to bottom when their event fires.
        </p>
        {canManage ? (
          <Button size="sm" onClick={openNew}>
            <Plus className="size-4" /> New rule
          </Button>
        ) : null}
      </div>

      {rules.isLoading ? (
        <div className="grid gap-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : rules.isError ? (
        <Card className="p-6 text-center text-sm text-muted-foreground">
          Could not load rules.{' '}
          <Button variant="link" className="px-1" onClick={() => rules.refetch()}>
            Retry
          </Button>
        </Card>
      ) : !rules.data || rules.data.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Workflow />
            </EmptyMedia>
            <EmptyTitle>No automation rules yet</EmptyTitle>
            <EmptyDescription>
              Automate assignment, tagging, replies and more when events happen.
            </EmptyDescription>
          </EmptyHeader>
          {canManage ? (
            <EmptyContent>
              <Button onClick={openNew}>
                <Plus className="size-4" /> Create your first rule
              </Button>
            </EmptyContent>
          ) : null}
        </Empty>
      ) : (
        <ul className="grid gap-3">
          {rules.data.map((rule, index) => (
            <li key={rule.id}>
              <Card className="flex flex-row items-center gap-4 p-4">
                {canManage ? (
                  <div className="flex flex-col">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      aria-label="Move up"
                      disabled={index === 0 || reorder.isPending}
                      onClick={() => move(index, -1)}
                    >
                      <ArrowUp className="size-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      aria-label="Move down"
                      disabled={index === rules.data.length - 1 || reorder.isPending}
                      onClick={() => move(index, 1)}
                    >
                      <ArrowDown className="size-3.5" />
                    </Button>
                  </div>
                ) : null}

                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{rule.name}</span>
                    <Badge variant="secondary">{eventLabel(rule.event)}</Badge>
                    {!rule.enabled ? <Badge variant="outline">Off</Badge> : null}
                  </div>
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {rule.conditions.length === 0
                      ? 'No conditions'
                      : rule.conditions.map((c) => describeCondition(c as never)).join(' AND ')}
                    {' · '}
                    {rule.actions.length} action{rule.actions.length === 1 ? '' : 's'}
                  </p>
                </div>

                <Switch
                  checked={rule.enabled}
                  disabled={!canManage || toggle.isPending}
                  aria-label={`Enable ${rule.name}`}
                  onCheckedChange={() => toggle.mutate(rule.id)}
                />
                {canManage ? (
                  <>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Edit ${rule.name}`}
                      onClick={() => openEdit(rule)}
                    >
                      <Pencil className="size-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${rule.name}`}
                      onClick={() => setDeleting(rule)}
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

      <RuleEditorDialog open={editorOpen} onOpenChange={setEditorOpen} rule={editing} />

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This rule will stop running. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleting) remove.mutate(deleting.id)
                setDeleting(null)
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
