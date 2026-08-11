import {
  ArrowLeft,
  BarChart3,
  MonitorPlay,
  PauseCircle,
  PlayCircle,
  Radio,
  Save,
  Trash2,
} from 'lucide-react'
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { useAuthStore, useHasPerm } from '@/stores/auth'

import type { TourKind } from '../api'
import { AudienceEditor } from '../components/AudienceEditor'
import { BannerDesigner } from '../components/BannerDesigner'
import { PreviewLinkCard } from '../components/PreviewLinkCard'
import { RecorderDialog } from '../components/RecorderDialog'
import { SandboxPlayer } from '../components/SandboxPlayer'
import { StepEditor } from '../components/StepEditor'
import { StepPreview } from '../components/StepPreview'
import { TourKindBadge, TourModeBadge } from '../components/TourBadges'
import { TourStatusBadge } from '../components/TourStatusBadge'
import { useDeleteTour, usePauseTour, usePublishTour, useTour, useUpdateTour } from '../hooks'
import {
  FREQUENCY_TYPES,
  MODES,
  serializeTour,
  toStepDraft,
  TOUR_KINDS,
  toTourDraft,
  validateSteps,
  type FrequencyType,
  type StepDraft,
  type TourDraft,
  type TourMode,
} from '../lib'
import { t } from '@/i18n'

function SettingSwitch({
  id,
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  id: string
  label: string
  hint: string
  checked: boolean
  disabled: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="grid gap-0.5">
        <Label htmlFor={id}>{label}</Label>
        <p className="text-[11px] text-muted-foreground">{hint}</p>
      </div>
      <Switch id={id} checked={checked} disabled={disabled} onCheckedChange={onChange} />
    </div>
  )
}

export function Component() {
  const { tourId } = useParams<{ tourId: string }>()
  const canManage = useHasPerm('tours:manage')
  const workspaceId = useAuthStore((state) => state.workspaceId) ?? ''
  const navigate = useNavigate()
  const tourQuery = useTour(tourId)
  const update = useUpdateTour()
  const publish = usePublishTour()
  const pause = usePauseTour()
  const remove = useDeleteTour()

  const [draft, setDraft] = useState<TourDraft | null>(null)
  const [steps, setSteps] = useState<StepDraft[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [recorderOpen, setRecorderOpen] = useState(false)
  const [sandboxOpen, setSandboxOpen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const tour = tourQuery.data
  useEffect(() => {
    if (!tour) return
    setDraft(toTourDraft(tour))
    setSteps((tour.steps ?? []).map(toStepDraft))
  }, [tour])

  function patch(values: Partial<TourDraft>) {
    setDraft((current) => (current ? { ...current, ...values } : current))
  }

  async function save() {
    if (!tourId || !draft) return
    if (!draft.name.trim()) {
      toast.error(t('tours.the_tour_needs_a_name'))
      return
    }
    const problem = validateSteps(steps)
    if (problem) {
      toast.error(problem)
      return
    }
    await update.mutateAsync({ id: tourId, body: serializeTour(draft, steps) })
  }

  if (tourQuery.isLoading || (tour && !draft)) {
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

  if (tourQuery.isError || !tour || !draft) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card className="p-6 text-center text-sm text-muted-foreground">
          Could not load this tour.{' '}
          <Button variant="link" asChild className="px-1">
            <Link to="/tours">{t('tours.back_to_tours')}</Link>
          </Button>
        </Card>
      </div>
    )
  }

  const selected = steps[selectedIndex] ?? steps[0] ?? null
  // Banner styling matters for a `banner` tour, and for any flow that contains
  // a banner step — hiding it behind the tour kind alone would strand those.
  const showsBanner = draft.kind === 'banner' || steps.some((step) => step.type === 'banner')

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b px-6 py-4">
        <Button variant="ghost" size="icon" asChild aria-label={t('tours.back_to_tours')}>
          <Link to="/tours">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-lg font-semibold">{draft.name || 'Untitled tour'}</h1>
            <TourStatusBadge status={tour.status} />
            <TourKindBadge kind={draft.kind} />
            <TourModeBadge mode={draft.mode} />
            <span className="text-xs text-muted-foreground">v{tour.version}</span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link to={`/tours/${tour.id}/analytics`}>
              <BarChart3 className="size-4" /> {t('tours.analytics')}
            </Link>
          </Button>
          <Button variant="outline" size="sm" onClick={() => setSandboxOpen(true)}>
            <MonitorPlay className="size-4" /> {t('tours.sandbox')}
          </Button>
          {canManage ? (
            <>
              <Button variant="outline" size="sm" onClick={() => setRecorderOpen(true)}>
                <Radio className="size-4" /> {t('tours.connect_recorder')}
              </Button>
              {tour.status === 'live' ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={pause.isPending}
                  onClick={() => pause.mutate(tour.id)}
                >
                  <PauseCircle className="size-4" /> {t('common.pause')}
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={publish.isPending}
                  onClick={() => publish.mutate(tour.id)}
                >
                  <PlayCircle className="size-4" /> {t('common.publish')}
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon"
                aria-label={t('tours.delete_tour')}
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 className="size-4" />
              </Button>
              <Button size="sm" onClick={save} disabled={update.isPending}>
                <Save className="size-4" /> {update.isPending ? 'Saving…' : 'Save'}
              </Button>
            </>
          ) : null}
        </div>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[340px_1fr]">
          <div className="grid content-start gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t('common.settings')}</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="tour-name">{t('common.name')}</Label>
                  <Input
                    id="tour-name"
                    value={draft.name}
                    disabled={!canManage}
                    onChange={(e) => patch({ name: e.target.value })}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="tour-desc">{t('common.description')}</Label>
                  <Textarea
                    id="tour-desc"
                    rows={2}
                    value={draft.description}
                    disabled={!canManage}
                    onChange={(e) => patch({ description: e.target.value })}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="tour-kind">{t('tours.kind')}</Label>
                  <NativeSelect
                    id="tour-kind"
                    className="w-full"
                    value={draft.kind}
                    disabled={!canManage}
                    onChange={(e) => patch({ kind: e.target.value as TourKind })}
                  >
                    {TOUR_KINDS.map((option) => (
                      <NativeSelectOption key={option.value} value={option.value}>
                        {option.label}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="tour-trigger">{t('common.trigger')}</Label>
                  <NativeSelect
                    id="tour-trigger"
                    className="w-full"
                    value={draft.triggerType}
                    disabled={!canManage}
                    onChange={(e) =>
                      patch({ triggerType: e.target.value as 'manual' | 'url_match' })
                    }
                  >
                    <NativeSelectOption value="manual">{t('common.manual_api')}</NativeSelectOption>
                    <NativeSelectOption value="url_match">{t('common.on_url_match')}</NativeSelectOption>
                  </NativeSelect>
                </div>
                {draft.triggerType === 'url_match' ? (
                  <div className="grid gap-1.5">
                    <Label htmlFor="tour-url">{t('common.url_pattern')}</Label>
                    <Input
                      id="tour-url"
                      className="font-mono text-xs"
                      placeholder="/dashboard*"
                      value={draft.urlPattern}
                      disabled={!canManage}
                      onChange={(e) => patch({ urlPattern: e.target.value })}
                    />
                  </div>
                ) : null}
                <div className="grid gap-1.5">
                  <Label htmlFor="tour-accent">{t('common.accent_color')}</Label>
                  <div className="flex items-center gap-2">
                    <input
                      id="tour-accent"
                      type="color"
                      className="h-9 w-12 cursor-pointer rounded-md border bg-transparent"
                      value={draft.accent}
                      disabled={!canManage}
                      onChange={(e) => patch({ accent: e.target.value })}
                    />
                    <Input
                      aria-label={t('common.accent_hex')}
                      className="font-mono text-xs"
                      value={draft.accent}
                      disabled={!canManage}
                      onChange={(e) => patch({ accent: e.target.value })}
                    />
                  </div>
                </div>
              </CardContent>
            </Card>

            {showsBanner ? (
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">{t('tours.banner_design')}</CardTitle>
                </CardHeader>
                <CardContent>
                  <BannerDesigner draft={draft} disabled={!canManage} onChange={patch} />
                </CardContent>
              </Card>
            ) : null}

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t('common.audience')}</CardTitle>
              </CardHeader>
              <CardContent>
                <AudienceEditor
                  type={draft.audienceType}
                  filters={draft.filters}
                  disabled={!canManage}
                  onTypeChange={(audienceType) => patch({ audienceType })}
                  onFiltersChange={(filters) => patch({ filters })}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t('tours.scheduling')}</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="start-at">{t('common.starts')}</Label>
                  <Input
                    id="start-at"
                    type="datetime-local"
                    value={draft.startAt}
                    disabled={!canManage}
                    onChange={(e) => patch({ startAt: e.target.value })}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="end-at">{t('common.ends')}</Label>
                  <Input
                    id="end-at"
                    type="datetime-local"
                    value={draft.endAt}
                    disabled={!canManage}
                    onChange={(e) => patch({ endAt: e.target.value })}
                  />
                  <p className="text-[11px] text-muted-foreground">
                    {t('common.leave_both_empty_to_run_continuously')}
                  </p>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="frequency">{t('tours.show_it')}</Label>
                  <NativeSelect
                    id="frequency"
                    className="w-full"
                    value={draft.frequencyType}
                    disabled={!canManage}
                    onChange={(e) => patch({ frequencyType: e.target.value as FrequencyType })}
                  >
                    {FREQUENCY_TYPES.map((option) => (
                      <NativeSelectOption key={option.value} value={option.value}>
                        {option.label}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                </div>
                {draft.frequencyType === 'every_time' ? (
                  <div className="grid gap-1.5">
                    <Label htmlFor="cooldown">Cooldown (hours)</Label>
                    <Input
                      id="cooldown"
                      type="number"
                      min={1}
                      placeholder={t('tours.no_cooldown')}
                      value={draft.cooldownHours}
                      disabled={!canManage}
                      onChange={(e) => patch({ cooldownHours: e.target.value })}
                    />
                  </div>
                ) : null}
                <div className="grid gap-1.5">
                  <Label htmlFor="priority">{t('common.priority')}</Label>
                  <Input
                    id="priority"
                    type="number"
                    value={draft.priority}
                    disabled={!canManage}
                    onChange={(e) => patch({ priority: e.target.value })}
                  />
                  <p className="text-[11px] text-muted-foreground">
                    {t('common.higher_wins_when_several_experiences_match')}
                  </p>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t('tours.behaviour')}</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="mode">{t('tours.mode')}</Label>
                  <NativeSelect
                    id="mode"
                    className="w-full"
                    value={draft.mode}
                    disabled={!canManage}
                    onChange={(e) => patch({ mode: e.target.value as TourMode })}
                  >
                    {MODES.map((option) => (
                      <NativeSelectOption key={option.value} value={option.value}>
                        {option.label}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                  <p className="text-[11px] text-muted-foreground">
                    {MODES.find((option) => option.value === draft.mode)?.hint}
                  </p>
                </div>
                <SettingSwitch
                  id="backdrop"
                  label={t('tours.backdrop')}
                  hint="Dim the page around the highlighted element."
                  checked={draft.backdrop}
                  disabled={!canManage}
                  onChange={(backdrop) => patch({ backdrop })}
                />
                <SettingSwitch
                  id="show-progress"
                  label={t('tours.show_progress')}
                  hint="Display “2 of 5” and a progress bar."
                  checked={draft.showProgress}
                  disabled={!canManage}
                  onChange={(showProgress) => patch({ showProgress })}
                />
                <SettingSwitch
                  id="dismissable"
                  label={t('tours.dismissable')}
                  hint="Let people close it with × or Esc."
                  checked={draft.dismissable}
                  disabled={!canManage}
                  onChange={(dismissable) => patch({ dismissable })}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">{t('common.preview')}</CardTitle>
              </CardHeader>
              <CardContent>
                <StepPreview
                  draft={selected}
                  accent={draft.accent}
                  index={selected ? steps.indexOf(selected) : 0}
                  total={steps.length}
                  showProgress={draft.showProgress}
                />
              </CardContent>
            </Card>

            <PreviewLinkCard tourId={tour.id} disabled={!canManage} />
          </div>

          <div>
            <h2 className="mb-3 text-sm font-medium">{t('tours.steps')}</h2>
            <StepEditor
              steps={steps}
              onChange={setSteps}
              disabled={!canManage}
              selectedKey={selected?.key ?? null}
              onSelect={(key) => setSelectedIndex(steps.findIndex((step) => step.key === key))}
            />
          </div>
        </div>
      </div>

      <RecorderDialog open={recorderOpen} onOpenChange={setRecorderOpen} />

      <Dialog open={sandboxOpen} onOpenChange={setSandboxOpen}>
        <DialogContent className="sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>{t('tours.sandbox')}</DialogTitle>
            <DialogDescription>
              {t('tours.walk_the_tour_against_the_screens')}
            </DialogDescription>
          </DialogHeader>
          <SandboxPlayer
            steps={steps}
            accent={draft.accent}
            workspaceId={workspaceId}
            showProgress={draft.showProgress}
          />
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{tour.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              {t('tours.this_permanently_removes_the_tour_and')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                await remove.mutateAsync(tour.id)
                setConfirmDelete(false)
                navigate('/tours')
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

export default Component
