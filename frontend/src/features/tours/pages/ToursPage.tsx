import { BarChart3, Copy, Map as MapIcon, Plus, Radio } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useHasPerm } from '@/stores/auth'

import type { Tour, TourKind } from '../api'
import { RecorderDialog } from '../components/RecorderDialog'
import { Sparkline } from '../components/Sparkline'
import { TourKindBadge, TourModeBadge } from '../components/TourBadges'
import { TourStatusBadge } from '../components/TourStatusBadge'
import { useCreateTour, useDuplicateTour, useTours, useTourStats } from '../hooks'
import { formatRate, TOUR_KINDS } from '../lib'
import { t } from '@/i18n'

const KIND_TABS: { value: 'all' | TourKind; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'flow', label: 'Flows' },
  { value: 'banner', label: 'Banners' },
  { value: 'announcement', label: 'Announcements' },
]

function TourStatsRow({ tour }: { tour: Tour }) {
  const stats = useTourStats(tour.id, tour.status !== 'draft')
  if (tour.status === 'draft') {
    return <p className="text-xs text-muted-foreground">{t('common.not_published_yet')}</p>
  }
  if (stats.isLoading) return <Skeleton className="h-6 w-32" />
  if (!stats.data) return <p className="text-xs text-muted-foreground">{t('common.no_data')}</p>
  return (
    <div className="flex items-center gap-4">
      <div>
        <div className="text-sm font-semibold tabular-nums">{stats.data.starts}</div>
        <div className="text-[11px] text-muted-foreground">{t('tours.starts')}</div>
      </div>
      <div>
        <div className="text-sm font-semibold tabular-nums">
          {formatRate(stats.data.completion_rate)}
        </div>
        <div className="text-[11px] text-muted-foreground">{t('common.completed')}</div>
      </div>
      <Sparkline values={stats.data.steps.map((s) => s.viewed)} className="ml-auto" />
    </div>
  )
}

function TourCard({ tour, canManage }: { tour: Tour; canManage: boolean }) {
  const duplicate = useDuplicateTour()
  const stepCount = tour.steps?.length ?? 0

  return (
    <Card data-testid="tour-card">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="truncate">
            <Link to={`/tours/${tour.id}`} className="hover:underline">
              {tour.name}
            </Link>
          </CardTitle>
          <TourStatusBadge status={tour.status} />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <TourKindBadge kind={tour.kind} />
          <TourModeBadge mode={tour.settings?.mode} />
        </div>
        <p className="text-xs text-muted-foreground">
          {stepCount} step{stepCount === 1 ? '' : 's'}
          {tour.description ? ` · ${tour.description}` : ''}
        </p>
      </CardHeader>
      <CardContent className="grid gap-3">
        <TourStatsRow tour={tour} />
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link to={`/tours/${tour.id}/analytics`}>
              <BarChart3 className="size-4" /> {t('tours.analytics')}
            </Link>
          </Button>
          {canManage ? (
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Duplicate ${tour.name}`}
              disabled={duplicate.isPending}
              onClick={() => duplicate.mutate(tour)}
            >
              <Copy className="size-4" /> {t('tours.duplicate')}
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}

export function Component() {
  const canManage = useHasPerm('tours:manage')
  const tours = useTours()
  const create = useCreateTour()
  const navigate = useNavigate()

  const [kind, setKind] = useState<'all' | TourKind>('all')
  const [dialogOpen, setDialogOpen] = useState(false)
  const [recorderOpen, setRecorderOpen] = useState(false)
  const [name, setName] = useState('')
  const [newKind, setNewKind] = useState<TourKind>('flow')

  const visible = (tours.data ?? []).filter((tour) => kind === 'all' || tour.kind === kind)

  async function submit() {
    if (!name.trim()) return
    try {
      const tour = await create.mutateAsync({
        name: name.trim(),
        description: '',
        kind: newKind,
        priority: 0,
      })
      setDialogOpen(false)
      setName('')
      navigate(`/tours/${tour.id}`)
    } catch {
      /* toast handled in hook */
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-4 border-b px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold">{t('tours.product_tours')}</h1>
          <p className="text-sm text-muted-foreground">
            {t('tours.guide_users_through_your_product_with')}
          </p>
        </div>
        {canManage ? (
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => setDialogOpen(true)}>
              <Plus className="size-4" /> {t('tours.new_tour')}
            </Button>
            <Button onClick={() => setRecorderOpen(true)}>
              <Radio className="size-4" /> {t('tours.record_a_tour')}
            </Button>
          </div>
        ) : null}
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-4xl">
          <Tabs value={kind} onValueChange={(value) => setKind(value as 'all' | TourKind)}>
            <TabsList>
              {KIND_TABS.map((tab) => (
                <TabsTrigger key={tab.value} value={tab.value}>
                  {tab.label}
                </TabsTrigger>
              ))}
            </TabsList>
            {/* Controlled value ⇒ exactly one panel, always the active one. */}
            <TabsContent value={kind} className="mt-4">
              {tours.isLoading ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  {[0, 1, 2, 3].map((i) => (
                    <Skeleton key={i} className="h-48 w-full" />
                  ))}
                </div>
              ) : tours.isError ? (
                <Card className="p-6 text-center text-sm text-muted-foreground">
                  Could not load tours.{' '}
                  <Button variant="link" className="px-1" onClick={() => tours.refetch()}>
                    {t('common.retry')}
                  </Button>
                </Card>
              ) : visible.length === 0 ? (
                <Empty>
                  <EmptyHeader>
                    <EmptyMedia variant="icon">
                      <MapIcon />
                    </EmptyMedia>
                    <EmptyTitle>
                      {kind === 'all'
                        ? 'No tours yet'
                        : `No ${KIND_TABS.find((t) => t.value === kind)?.label.toLowerCase()} yet`}
                    </EmptyTitle>
                    <EmptyDescription>
                      {t('tours.the_fastest_way_to_build_one')}
                    </EmptyDescription>
                  </EmptyHeader>
                  {canManage ? (
                    <EmptyContent>
                      <div className="flex flex-wrap items-center justify-center gap-2">
                        <Button onClick={() => setRecorderOpen(true)}>
                          <Radio className="size-4" /> {t('tours.record_a_tour')}
                        </Button>
                        <Button variant="outline" onClick={() => setDialogOpen(true)}>
                          <Plus className="size-4" /> {t('tours.build_one_by_hand')}
                        </Button>
                      </div>
                    </EmptyContent>
                  ) : null}
                </Empty>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2">
                  {visible.map((tour) => (
                    <TourCard key={tour.id} tour={tour} canManage={canManage} />
                  ))}
                </div>
              )}
            </TabsContent>
          </Tabs>
        </div>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('tours.new_tour')}</DialogTitle>
            <DialogDescription>
              {t('tours.pick_what_you_want_to_ship')}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 py-2">
            <div className="grid gap-2">
              <Label htmlFor="tour-name">{t('common.name')}</Label>
              <Input
                id="tour-name"
                value={name}
                placeholder={t('tours.onboarding_walkthrough')}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void submit()
                }}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="tour-kind">{t('tours.kind')}</Label>
              <NativeSelect
                id="tour-kind"
                className="w-full"
                value={newKind}
                onChange={(e) => setNewKind(e.target.value as TourKind)}
              >
                {TOUR_KINDS.map((option) => (
                  <NativeSelectOption key={option.value} value={option.value}>
                    {option.label}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
              <p className="text-xs text-muted-foreground">
                {TOUR_KINDS.find((option) => option.value === newKind)?.hint}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button onClick={submit} disabled={!name.trim() || create.isPending}>
              {create.isPending ? 'Creating…' : 'Create tour'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <RecorderDialog open={recorderOpen} onOpenChange={setRecorderOpen} />
    </div>
  )
}

export default Component
