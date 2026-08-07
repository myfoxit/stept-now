import { Pencil, Plus, Timer, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { useHasPerm } from '@/stores/auth'

import type { SlaPolicy, SlaPolicyCreate } from '../api'
import {
  useCreateSlaPolicy,
  useDeleteSlaPolicy,
  useSlaPolicies,
  useUpdateSlaPolicy,
} from '../hooks'

const THRESHOLDS = [
  { key: 'first_response_minutes', label: 'First response (minutes)' },
  { key: 'next_response_minutes', label: 'Next response (minutes)' },
  { key: 'resolution_minutes', label: 'Resolution (minutes)' },
] as const

type ThresholdKey = (typeof THRESHOLDS)[number]['key']

function minutesLabel(minutes: number): string {
  if (minutes % 60 === 0 && minutes >= 60) return `${minutes / 60}h`
  return `${minutes}m`
}

function thresholdSummary(policy: SlaPolicy): string {
  const parts: string[] = []
  if (policy.first_response_minutes != null)
    parts.push(`First response ${minutesLabel(policy.first_response_minutes)}`)
  if (policy.next_response_minutes != null)
    parts.push(`Next response ${minutesLabel(policy.next_response_minutes)}`)
  if (policy.resolution_minutes != null)
    parts.push(`Resolution ${minutesLabel(policy.resolution_minutes)}`)
  return parts.join(' · ')
}

function EditorDialog({
  open,
  onOpenChange,
  policy,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  policy?: SlaPolicy | null
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [businessHoursOnly, setBusinessHoursOnly] = useState(false)
  const [thresholds, setThresholds] = useState<Record<ThresholdKey, string>>({
    first_response_minutes: '',
    next_response_minutes: '',
    resolution_minutes: '',
  })

  const create = useCreateSlaPolicy()
  const update = useUpdateSlaPolicy()
  const saving = create.isPending || update.isPending

  // Re-seed the form each time the dialog opens (matches RuleEditorDialog).
  useEffect(() => {
    if (!open) return
    setName(policy?.name ?? '')
    setDescription(policy?.description ?? '')
    setBusinessHoursOnly(policy?.only_during_business_hours ?? false)
    setThresholds({
      first_response_minutes:
        policy?.first_response_minutes != null ? String(policy.first_response_minutes) : '',
      next_response_minutes:
        policy?.next_response_minutes != null ? String(policy.next_response_minutes) : '',
      resolution_minutes:
        policy?.resolution_minutes != null ? String(policy.resolution_minutes) : '',
    })
  }, [open, policy])

  function parsed(key: ThresholdKey): number | null {
    const raw = thresholds[key].trim()
    if (raw === '') return null
    const value = Number(raw)
    return Number.isFinite(value) && value > 0 ? Math.round(value) : null
  }

  const hasThreshold = THRESHOLDS.some((t) => parsed(t.key) !== null)
  const canSave = name.trim().length > 0 && hasThreshold

  async function save() {
    const body: SlaPolicyCreate = {
      name: name.trim(),
      description: description.trim() || null,
      first_response_minutes: parsed('first_response_minutes'),
      next_response_minutes: parsed('next_response_minutes'),
      resolution_minutes: parsed('resolution_minutes'),
      only_during_business_hours: businessHoursOnly,
    }
    try {
      if (policy) {
        await update.mutateAsync({ id: policy.id, body })
      } else {
        await create.mutateAsync(body)
      }
      onOpenChange(false)
    } catch {
      /* toast handled in hook */
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{policy ? 'Edit SLA policy' : 'New SLA policy'}</DialogTitle>
          <DialogDescription>
            Target times for responses and resolution. Leave a field empty to skip it.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor="sla-name">Name</Label>
            <Input
              id="sla-name"
              placeholder="e.g. Premium support"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="sla-description">Description</Label>
            <Input
              id="sla-description"
              placeholder="Optional"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          {THRESHOLDS.map((t) => (
            <div key={t.key} className="grid gap-1.5">
              <Label htmlFor={`sla-${t.key}`}>{t.label}</Label>
              <Input
                id={`sla-${t.key}`}
                type="number"
                min={1}
                placeholder="e.g. 30"
                value={thresholds[t.key]}
                onChange={(e) => setThresholds((prev) => ({ ...prev, [t.key]: e.target.value }))}
              />
            </div>
          ))}
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-primary"
              checked={businessHoursOnly}
              onChange={(e) => setBusinessHoursOnly(e.target.checked)}
              aria-label="Count business hours only"
            />
            <span>
              <span className="font-medium">Count business hours only</span>
              <span className="block text-xs text-muted-foreground">
                Nights and weekends are excluded, using the inbox&rsquo;s working hours.
              </span>
            </span>
          </label>
          {!hasThreshold ? (
            <p className="text-xs text-destructive">Set at least one target time.</p>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={!canSave || saving}>
            {saving ? 'Saving…' : policy ? 'Save changes' : 'Create policy'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function SlaPanel() {
  const canManage = useHasPerm('automations:manage')
  const policies = useSlaPolicies()
  const remove = useDeleteSlaPolicy()

  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<SlaPolicy | null>(null)
  const [deleting, setDeleting] = useState<SlaPolicy | null>(null)

  function openNew() {
    setEditing(null)
    setEditorOpen(true)
  }
  function openEdit(policy: SlaPolicy) {
    setEditing(policy)
    setEditorOpen(true)
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Response and resolution targets you can apply to conversations.
        </p>
        {canManage ? (
          <Button size="sm" onClick={openNew}>
            <Plus className="size-4" /> New policy
          </Button>
        ) : null}
      </div>

      {policies.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : policies.isError ? (
        <Card className="p-6 text-center text-sm text-muted-foreground">
          Could not load SLA policies.{' '}
          <Button variant="link" className="px-1" onClick={() => policies.refetch()}>
            Retry
          </Button>
        </Card>
      ) : !policies.data || policies.data.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Timer />
            </EmptyMedia>
            <EmptyTitle>No SLA policies yet</EmptyTitle>
            <EmptyDescription>
              Define response-time targets and apply them to conversations.
            </EmptyDescription>
          </EmptyHeader>
          {canManage ? (
            <EmptyContent>
              <Button onClick={openNew}>
                <Plus className="size-4" /> Create a policy
              </Button>
            </EmptyContent>
          ) : null}
        </Empty>
      ) : (
        <ul className="grid gap-3">
          {policies.data.map((policy) => (
            <li key={policy.id}>
              <Card className="flex flex-row items-center gap-4 p-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{policy.name}</span>
                    <Badge variant="secondary" className="gap-1">
                      <Timer className="size-3" />
                      SLA
                    </Badge>
                  </div>
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {thresholdSummary(policy)}
                    {policy.description ? ` — ${policy.description}` : ''}
                  </p>
                </div>
                {canManage ? (
                  <>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Edit ${policy.name}`}
                      onClick={() => openEdit(policy)}
                    >
                      <Pencil className="size-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${policy.name}`}
                      onClick={() => setDeleting(policy)}
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

      <EditorDialog open={editorOpen} onOpenChange={setEditorOpen} policy={editing} />

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Conversations currently under this policy keep their history, but no new targets will
              be tracked. This cannot be undone.
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
