import { BarChart3, Copy, Map as MapIcon, Plus } from 'lucide-react'
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
import { Sparkline } from '../components/Sparkline'
import { TourKindBadge, TourModeBadge } from '../components/TourBadges'
import { TourStatusBadge } from '../components/TourStatusBadge'
import { useCreateTour, useDuplicateTour, useTours, useTourStats } from '../hooks'
import { formatRate, TOUR_KINDS } from '../lib'

const KIND_TABS: { value: 'all' | TourKind; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'flow', label: 'Flows' },
  { value: 'banner', label: 'Banners' },
  { value: 'announcement', label: 'Announcements' },
]

function TourStatsRow({ tour }: { tour: Tour }) {
  const stats = useTourStats(tour.id, tour.status !== 'draft')
  if (tour.status === 'draft') {
    return <p className="text-xs text-muted-foreground">Not published yet</p>
  }
  if (stats.isLoading) return <Skeleton className="h-6 w-32" />
  if (!stats.data) return <p className="text-xs text-muted-foreground">No data</p>
  return (
    <div className="flex items-center gap-4">
      <div>
        <div className="text-sm font-semibold tabular-nums">{stats.data.starts}</div>
        <div className="text-[11px] text-muted-foreground">starts</div>
      </div>
      <div>
        <div className="text-sm font-semibold tabular-nums">
          {formatRate(stats.data.completion_rate)}
        </div>
        <div className="text-[11px] text-muted-foreground">completed</div>
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
              <BarChart3 className="size-4" /> Analytics
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
              <Copy className="size-4" /> Duplicate
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
          <h1 className="text-lg font-semibold">Product tours</h1>
          <p className="text-sm text-muted-foreground">
            Guide users through your product with tours, banners and announcements.
          </p>
        </div>
        {canManage ? (
          <Button onClick={() => setDialogOpen(true)}>
            <Plus className="size-4" /> New tour
          </Button>
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
                    Retry
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
                      Build a guided walkthrough, ship a banner, or record one from your live app.
                    </EmptyDescription>
                  </EmptyHeader>
                  {canManage ? (
                    <EmptyContent>
                      <Button onClick={() => setDialogOpen(true)}>
                        <Plus className="size-4" /> Create a tour
                      </Button>
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
            <DialogTitle>New tour</DialogTitle>
            <DialogDescription>
              Pick what you want to ship — every kind runs on the same engine.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 py-2">
            <div className="grid gap-2">
              <Label htmlFor="tour-name">Name</Label>
              <Input
                id="tour-name"
                value={name}
                placeholder="Onboarding walkthrough"
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void submit()
                }}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="tour-kind">Kind</Label>
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
              Cancel
            </Button>
            <Button onClick={submit} disabled={!name.trim() || create.isPending}>
              {create.isPending ? 'Creating…' : 'Create tour'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default Component
