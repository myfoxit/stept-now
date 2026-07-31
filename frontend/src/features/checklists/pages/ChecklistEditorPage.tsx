import { ArrowLeft, PauseCircle, PlayCircle, Save, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'

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
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { useHasPerm } from '@/stores/auth'

import type { ChecklistPosition } from '../api'
import { AudienceEditor } from '../components/AudienceEditor'
import { ChecklistStatusBadge } from '../components/ChecklistStatusBadge'
import { ItemEditor } from '../components/ItemEditor'
import {
  useChecklist,
  useChecklistStats,
  useDeleteChecklist,
  usePauseChecklist,
  usePublishChecklist,
  useTourOptions,
  useUpdateChecklist,
} from '../hooks'
import {
  formatRate,
  POSITIONS,
  serializeFilters,
  serializeItem,
  toFilterDraft,
  toItemDraft,
  validateItems,
  type ChecklistItemDraft,
  type FilterDraft,
} from '../lib'

function StatsStrip({ checklistId, published }: { checklistId: string; published: boolean }) {
  const stats = useChecklistStats(checklistId, published)

  if (!published) {
    return (
      <Card className="p-4 text-xs text-muted-foreground">
        Publish this checklist to start collecting completion stats.
      </Card>
    )
  }
  if (stats.isLoading) return <Skeleton className="h-20 w-full" />
  if (stats.isError || !stats.data) {
    return (
      <Card className="p-4 text-center text-xs text-muted-foreground">
        Could not load stats.{' '}
        <Button variant="link" className="px-1 text-xs" onClick={() => stats.refetch()}>
          Retry
        </Button>
      </Card>
    )
  }

  return (
    <Card className="gap-3 p-4">
      <div className="flex flex-wrap items-center gap-6">
        <div>
          <div className="text-2xl font-semibold tabular-nums">{stats.data.starts}</div>
          <div className="text-xs text-muted-foreground">started</div>
        </div>
        <div>
          <div className="text-2xl font-semibold tabular-nums">{stats.data.completions}</div>
          <div className="text-xs text-muted-foreground">completed</div>
        </div>
        <div>
          <div className="text-2xl font-semibold tabular-nums">
            {formatRate(stats.data.completion_rate)}
          </div>
          <div className="text-xs text-muted-foreground">completion rate</div>
        </div>
      </div>
      {stats.data.items.length > 0 ? (
        <ul className="grid gap-1 text-xs text-muted-foreground">
          {stats.data.items.map((item) => (
            <li key={item.id} className="flex items-center justify-between gap-3">
              <span className="truncate">{item.title}</span>
              <span className="tabular-nums">{item.completed_count} completed</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  )
}

export function Component() {
  const { checklistId } = useParams<{ checklistId: string }>()
  const canManage = useHasPerm('tours:manage')
  const navigate = useNavigate()

  const checklistQuery = useChecklist(checklistId)
  const tours = useTourOptions()
  const update = useUpdateChecklist()
  const publish = usePublishChecklist()
  const pause = usePauseChecklist()
  const remove = useDeleteChecklist()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [launcherLabel, setLauncherLabel] = useState('Getting started')
  const [autoOpenOnce, setAutoOpenOnce] = useState(true)
  const [accent, setAccent] = useState('#6366f1')
  const [position, setPosition] = useState<ChecklistPosition>('bottom-right')
  const [triggerType, setTriggerType] = useState<'manual' | 'url_match'>('url_match')
  const [urlPattern, setUrlPattern] = useState('*')
  const [audienceType, setAudienceType] = useState<'all' | 'filters'>('all')
  const [filters, setFilters] = useState<FilterDraft[]>([])
  const [priority, setPriority] = useState(0)
  const [items, setItems] = useState<ChecklistItemDraft[]>([])
  const [confirmDelete, setConfirmDelete] = useState(false)

  const checklist = checklistQuery.data
  useEffect(() => {
    if (!checklist) return
    setName(checklist.name)
    setDescription(checklist.description ?? '')
    setLauncherLabel(checklist.launcher?.label ?? 'Getting started')
    setAutoOpenOnce(checklist.launcher?.auto_open_once ?? true)
    setAccent(checklist.theme?.accent ?? '#6366f1')
    setPosition(checklist.theme?.position ?? 'bottom-right')
    setTriggerType(checklist.trigger?.type ?? 'url_match')
    setUrlPattern(checklist.trigger?.url_pattern ?? '*')
    setAudienceType(checklist.audience?.type ?? 'all')
    setFilters((checklist.audience?.filters ?? []).map(toFilterDraft))
    setPriority(checklist.priority ?? 0)
    setItems((checklist.items ?? []).map(toItemDraft))
  }, [checklist])

  async function save() {
    if (!checklistId) return
    const problem = validateItems(items)
    if (problem) {
      toast.error(problem)
      return
    }
    await update.mutateAsync({
      id: checklistId,
      body: {
        name: name.trim(),
        description,
        items: items.map(serializeItem),
        trigger: {
          type: triggerType,
          url_pattern: triggerType === 'url_match' ? urlPattern.trim() || '*' : null,
        },
        audience: {
          type: audienceType,
          filters: audienceType === 'filters' ? serializeFilters(filters) : [],
        },
        theme: { accent, position },
        launcher: { label: launcherLabel.trim() || 'Getting started', auto_open_once: autoOpenOnce },
        priority,
      },
    })
  }

  if (checklistQuery.isLoading) {
    return (
      <div className="flex h-full flex-col overflow-hidden">
        <div className="border-b px-6 py-4">
          <Skeleton className="h-8 w-64" />
        </div>
        <div className="flex-1 space-y-3 p-6">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    )
  }

  if (checklistQuery.isError || !checklist) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card className="p-6 text-center text-sm text-muted-foreground">
          Could not load this checklist.{' '}
          <Button variant="link" asChild className="px-1">
            <Link to="/checklists">Back to checklists</Link>
          </Button>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b px-6 py-4">
        <Button variant="ghost" size="icon" asChild aria-label="Back to checklists">
          <Link to="/checklists">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-lg font-semibold">{name || 'Untitled checklist'}</h1>
            <ChecklistStatusBadge status={checklist.status} />
            <span className="text-xs text-muted-foreground">v{checklist.version}</span>
          </div>
        </div>

        {canManage ? (
          <div className="flex flex-wrap items-center gap-2">
            {checklist.status === 'live' ? (
              <Button
                variant="outline"
                size="sm"
                disabled={pause.isPending}
                onClick={() => pause.mutate(checklist.id)}
              >
                <PauseCircle className="size-4" /> Pause
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                disabled={publish.isPending}
                onClick={() => publish.mutate(checklist.id)}
              >
                <PlayCircle className="size-4" /> Publish
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              aria-label="Delete checklist"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 className="size-4" />
            </Button>
            <Button size="sm" onClick={save} disabled={update.isPending}>
              <Save className="size-4" /> {update.isPending ? 'Saving…' : 'Save'}
            </Button>
          </div>
        ) : null}
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[320px_1fr]">
          <div className="grid gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Settings</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="checklist-name">Name</Label>
                  <Input
                    id="checklist-name"
                    value={name}
                    disabled={!canManage}
                    onChange={(e) => setName(e.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="checklist-desc">Description</Label>
                  <Textarea
                    id="checklist-desc"
                    rows={2}
                    value={description}
                    disabled={!canManage}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="checklist-launcher">Launcher label</Label>
                  <Input
                    id="checklist-launcher"
                    value={launcherLabel}
                    disabled={!canManage}
                    onChange={(e) => setLauncherLabel(e.target.value)}
                  />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <Label htmlFor="checklist-auto-open">Auto-open once</Label>
                  <Switch
                    id="checklist-auto-open"
                    checked={autoOpenOnce}
                    disabled={!canManage}
                    aria-label="Auto-open once"
                    onCheckedChange={setAutoOpenOnce}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="checklist-accent">Accent color</Label>
                  <div className="flex items-center gap-2">
                    <input
                      id="checklist-accent"
                      type="color"
                      className="h-9 w-12 cursor-pointer rounded-md border bg-transparent"
                      value={accent}
                      disabled={!canManage}
                      onChange={(e) => setAccent(e.target.value)}
                    />
                    <Input
                      className="font-mono text-xs"
                      aria-label="Accent hex"
                      value={accent}
                      disabled={!canManage}
                      onChange={(e) => setAccent(e.target.value)}
                    />
                  </div>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="checklist-position">Position</Label>
                  <NativeSelect
                    id="checklist-position"
                    className="w-full"
                    value={position}
                    disabled={!canManage}
                    onChange={(e) => setPosition(e.target.value as ChecklistPosition)}
                  >
                    {POSITIONS.map((option) => (
                      <NativeSelectOption key={option.value} value={option.value}>
                        {option.label}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Targeting</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="checklist-trigger">Trigger</Label>
                  <NativeSelect
                    id="checklist-trigger"
                    className="w-full"
                    value={triggerType}
                    disabled={!canManage}
                    onChange={(e) => setTriggerType(e.target.value as 'manual' | 'url_match')}
                  >
                    <NativeSelectOption value="manual">Manual / API</NativeSelectOption>
                    <NativeSelectOption value="url_match">On URL match</NativeSelectOption>
                  </NativeSelect>
                </div>
                {triggerType === 'url_match' ? (
                  <div className="grid gap-1.5">
                    <Label htmlFor="checklist-url">URL pattern</Label>
                    <Input
                      id="checklist-url"
                      className="font-mono text-xs"
                      placeholder="*"
                      value={urlPattern}
                      disabled={!canManage}
                      onChange={(e) => setUrlPattern(e.target.value)}
                    />
                  </div>
                ) : null}

                <AudienceEditor
                  type={audienceType}
                  filters={filters}
                  disabled={!canManage}
                  onTypeChange={setAudienceType}
                  onFiltersChange={setFilters}
                />

                <div className="grid gap-1.5">
                  <Label htmlFor="checklist-priority">Priority</Label>
                  <Input
                    id="checklist-priority"
                    type="number"
                    min={-100}
                    max={100}
                    value={priority}
                    disabled={!canManage}
                    onChange={(e) => setPriority(Number(e.target.value))}
                  />
                  <p className="text-xs text-muted-foreground">
                    Higher wins when several experiences match the same page.
                  </p>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4">
            <StatsStrip checklistId={checklist.id} published={checklist.status !== 'draft'} />
            <div>
              <h2 className="mb-3 text-sm font-medium">Items</h2>
              <ItemEditor
                items={items}
                tours={tours.data ?? []}
                disabled={!canManage}
                onChange={setItems}
              />
            </div>
          </div>
        </div>
      </div>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{checklist.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently removes the checklist and every contact&rsquo;s progress. This cannot
              be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                await remove.mutateAsync(checklist.id)
                setConfirmDelete(false)
                navigate('/checklists')
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
