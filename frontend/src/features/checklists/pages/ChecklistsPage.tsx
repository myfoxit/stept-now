import { ListChecks, Pause, Play, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'

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

import type { Checklist } from '../api'
import { ChecklistStatusBadge } from '../components/ChecklistStatusBadge'
import {
  useChecklists,
  useChecklistStats,
  useCreateChecklist,
  useDeleteChecklist,
  usePauseChecklist,
  usePublishChecklist,
} from '../hooks'
import { describeTrigger, formatRate } from '../lib'

function ChecklistStatsRow({ checklist }: { checklist: Checklist }) {
  const stats = useChecklistStats(checklist.id, checklist.status !== 'draft')

  if (checklist.status === 'draft') {
    return <p className="text-xs text-muted-foreground">Not published yet</p>
  }
  if (stats.isLoading) return <Skeleton className="h-8 w-32" />
  if (!stats.data) return <p className="text-xs text-muted-foreground">No data</p>

  return (
    <div className="flex items-center gap-5">
      <div>
        <div className="text-sm font-semibold tabular-nums">{stats.data.starts}</div>
        <div className="text-[11px] text-muted-foreground">started</div>
      </div>
      <div>
        <div className="text-sm font-semibold tabular-nums">{stats.data.completions}</div>
        <div className="text-[11px] text-muted-foreground">completed</div>
      </div>
      <div>
        <div className="text-sm font-semibold tabular-nums">
          {formatRate(stats.data.completion_rate)}
        </div>
        <div className="text-[11px] text-muted-foreground">completion rate</div>
      </div>
    </div>
  )
}

function ChecklistCard({
  checklist,
  canManage,
  onDelete,
}: {
  checklist: Checklist
  canManage: boolean
  onDelete: (checklist: Checklist) => void
}) {
  const publish = usePublishChecklist()
  const pause = usePauseChecklist()

  return (
    <Card className="gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Link
          to={`/checklists/${checklist.id}`}
          className="truncate font-medium hover:underline focus-visible:underline"
        >
          {checklist.name}
        </Link>
        <ChecklistStatusBadge status={checklist.status} />
        <div className="flex-1" />
        {canManage ? (
          <>
            {checklist.status === 'live' ? (
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label={`Pause ${checklist.name}`}
                disabled={pause.isPending}
                onClick={() => pause.mutate(checklist.id)}
              >
                <Pause className="size-4" />
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label={`Publish ${checklist.name}`}
                disabled={publish.isPending}
                onClick={() => publish.mutate(checklist.id)}
              >
                <Play className="size-4" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              aria-label={`Delete ${checklist.name}`}
              onClick={() => onDelete(checklist)}
            >
              <Trash2 className="size-4" />
            </Button>
          </>
        ) : null}
      </div>

      <p className="text-xs text-muted-foreground">
        {checklist.items.length} item{checklist.items.length === 1 ? '' : 's'} ·{' '}
        {describeTrigger(checklist)}
      </p>

      <ChecklistStatsRow checklist={checklist} />
    </Card>
  )
}

export function Component() {
  const canRead = useHasPerm('tours:read')
  const canManage = useHasPerm('tours:manage')
  const checklists = useChecklists(canRead)
  const create = useCreateChecklist()
  const remove = useDeleteChecklist()
  const navigate = useNavigate()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [name, setName] = useState('')
  const [deleting, setDeleting] = useState<Checklist | null>(null)

  async function submit() {
    if (!name.trim()) return
    try {
      const checklist = await create.mutateAsync({ name: name.trim() })
      setDialogOpen(false)
      setName('')
      navigate(`/checklists/${checklist.id}`)
    } catch {
      /* toast handled in hook */
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-4 border-b px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold">Checklists</h1>
          <p className="text-sm text-muted-foreground">
            Onboarding to-do lists that guide new users to their first win.
          </p>
        </div>
        {canManage ? (
          <Button onClick={() => setDialogOpen(true)}>
            <Plus className="size-4" /> New checklist
          </Button>
        ) : null}
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-4xl">
          {!canRead ? (
            <p className="rounded-md border p-6 text-center text-sm text-muted-foreground">
              You don&rsquo;t have access to checklists.
            </p>
          ) : checklists.isLoading ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-36 w-full" />
              ))}
            </div>
          ) : checklists.isError ? (
            <Card className="p-6 text-center text-sm text-muted-foreground">
              Could not load checklists.{' '}
              <Button variant="link" className="px-1" onClick={() => checklists.refetch()}>
                Retry
              </Button>
            </Card>
          ) : !checklists.data || checklists.data.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <ListChecks />
                </EmptyMedia>
                <EmptyTitle>No checklists yet</EmptyTitle>
                <EmptyDescription>
                  Build a getting-started list — link items to tours, docs or the messenger.
                </EmptyDescription>
              </EmptyHeader>
              {canManage ? (
                <EmptyContent>
                  <Button onClick={() => setDialogOpen(true)}>
                    <Plus className="size-4" /> Create your first checklist
                  </Button>
                </EmptyContent>
              ) : null}
            </Empty>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2">
              {checklists.data.map((checklist) => (
                <li key={checklist.id}>
                  <ChecklistCard
                    checklist={checklist}
                    canManage={canManage}
                    onDelete={setDeleting}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New checklist</DialogTitle>
            <DialogDescription>
              Name it now — you&rsquo;ll add items and targeting in the editor.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor="checklist-name">Name</Label>
            <Input
              id="checklist-name"
              placeholder="Getting started"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submit()
              }}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={!name.trim() || create.isPending}>
              {create.isPending ? 'Creating…' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{deleting?.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              The checklist disappears from the widget and its progress is deleted. This cannot be
              undone.
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

export default Component
