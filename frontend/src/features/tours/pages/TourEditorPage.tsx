import { ArrowLeft, Eye, PauseCircle, PlayCircle, Radio, Save, Trash2 } from 'lucide-react'
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
import { Textarea } from '@/components/ui/textarea'
import { useHasPerm } from '@/stores/auth'

import { useDeleteTour, usePauseTour, usePublishTour, useTour, useUpdateTour } from '../hooks'
import { localStepId, serializeStep, type StepDraft } from '../lib'
import { RecorderDialog } from '../components/RecorderDialog'
import { StepEditor } from '../components/StepEditor'
import { TourStatusBadge } from '../components/TourStatusBadge'

export function Component() {
  const { tourId } = useParams<{ tourId: string }>()
  const canManage = useHasPerm('tours:manage')
  const navigate = useNavigate()
  const tourQuery = useTour(tourId)
  const update = useUpdateTour()
  const publish = usePublishTour()
  const pause = usePauseTour()
  const remove = useDeleteTour()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [triggerType, setTriggerType] = useState('manual')
  const [urlPattern, setUrlPattern] = useState('')
  const [accent, setAccent] = useState('#6366f1')
  const [steps, setSteps] = useState<StepDraft[]>([])
  const [recorderOpen, setRecorderOpen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const tour = tourQuery.data
  useEffect(() => {
    if (!tour) return
    setName(tour.name)
    setDescription(tour.description ?? '')
    setTriggerType(tour.trigger?.type ?? 'manual')
    setUrlPattern(tour.trigger?.url_pattern ?? '')
    setAccent(tour.theme?.accent ?? '#6366f1')
    setSteps(
      (tour.steps ?? []).map((step) => ({
        id: step.id ?? localStepId(),
        selector: step.selector,
        title: step.title,
        body: step.body,
        placement: step.placement,
      }))
    )
  }, [tour])

  async function save() {
    if (steps.some((step) => step.selector.trim() === '')) {
      toast.error('Every step needs a CSS selector')
      return
    }
    if (!tourId) return
    await update.mutateAsync({
      id: tourId,
      body: {
        name: name.trim(),
        description,
        trigger: {
          type: triggerType as 'manual' | 'url_match',
          url_pattern: triggerType === 'url_match' ? urlPattern : null,
        },
        theme: { accent },
        steps: steps.map(serializeStep),
      },
    })
  }

  if (tourQuery.isLoading) {
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

  if (tourQuery.isError || !tour) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card className="p-6 text-center text-sm text-muted-foreground">
          Could not load this tour.{' '}
          <Button variant="link" asChild className="px-1">
            <Link to="/tours">Back to tours</Link>
          </Button>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b px-6 py-4">
        <Button variant="ghost" size="icon" asChild aria-label="Back to tours">
          <Link to="/tours">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-lg font-semibold">{name || 'Untitled tour'}</h1>
            <TourStatusBadge status={tour.status} />
            <span className="text-xs text-muted-foreground">v{tour.version}</span>
          </div>
        </div>

        {canManage ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setRecorderOpen(true)}>
              <Radio className="size-4" /> Connect recorder
            </Button>
            {tour.status === 'live' ? (
              <Button
                variant="outline"
                size="sm"
                disabled={pause.isPending}
                onClick={() => pause.mutate(tour.id)}
              >
                <PauseCircle className="size-4" /> Pause
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                disabled={publish.isPending}
                onClick={() => publish.mutate(tour.id)}
              >
                <PlayCircle className="size-4" /> Publish
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              aria-label="Delete tour"
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
                  <Label htmlFor="tour-name">Name</Label>
                  <Input
                    id="tour-name"
                    value={name}
                    disabled={!canManage}
                    onChange={(e) => setName(e.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="tour-desc">Description</Label>
                  <Textarea
                    id="tour-desc"
                    rows={2}
                    value={description}
                    disabled={!canManage}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="tour-trigger">Trigger</Label>
                  <NativeSelect
                    id="tour-trigger"
                    className="w-full"
                    value={triggerType}
                    disabled={!canManage}
                    onChange={(e) => setTriggerType(e.target.value)}
                  >
                    <NativeSelectOption value="manual">Manual / API</NativeSelectOption>
                    <NativeSelectOption value="url_match">On URL match</NativeSelectOption>
                  </NativeSelect>
                </div>
                {triggerType === 'url_match' ? (
                  <div className="grid gap-1.5">
                    <Label htmlFor="tour-url">URL pattern</Label>
                    <Input
                      id="tour-url"
                      className="font-mono text-xs"
                      placeholder="/dashboard*"
                      value={urlPattern}
                      disabled={!canManage}
                      onChange={(e) => setUrlPattern(e.target.value)}
                    />
                  </div>
                ) : null}
                <div className="grid gap-1.5">
                  <Label htmlFor="tour-accent">Accent color</Label>
                  <div className="flex items-center gap-2">
                    <input
                      id="tour-accent"
                      type="color"
                      className="h-9 w-12 cursor-pointer rounded-md border bg-transparent"
                      value={accent}
                      disabled={!canManage}
                      onChange={(e) => setAccent(e.target.value)}
                    />
                    <Input
                      className="font-mono text-xs"
                      value={accent}
                      disabled={!canManage}
                      onChange={(e) => setAccent(e.target.value)}
                    />
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="flex gap-2 py-4 text-xs text-muted-foreground">
                <Eye className="size-4 shrink-0" />
                <span>
                  Preview runs inside the widget on your site. Publish to a staging inbox to try
                  the live experience.
                </span>
              </CardContent>
            </Card>
          </div>

          <div>
            <h2 className="mb-3 text-sm font-medium">Steps</h2>
            <StepEditor steps={steps} onChange={setSteps} disabled={!canManage} />
          </div>
        </div>
      </div>

      <RecorderDialog open={recorderOpen} onOpenChange={setRecorderOpen} />

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{tour.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently removes the tour and its stats. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                await remove.mutateAsync(tour.id)
                setConfirmDelete(false)
                navigate('/tours')
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
