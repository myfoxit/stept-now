import { Map as MapIcon, Plus } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'

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
import { Skeleton } from '@/components/ui/skeleton'
import { useHasPerm } from '@/stores/auth'

import type { Tour } from '../api'
import { useCreateTour, useTours, useTourStats } from '../hooks'
import { Sparkline } from '../components/Sparkline'
import { TourStatusBadge } from '../components/TourStatusBadge'

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
          {Math.round(stats.data.completion_rate * 100)}%
        </div>
        <div className="text-[11px] text-muted-foreground">completed</div>
      </div>
      <Sparkline values={stats.data.steps.map((s) => s.viewed)} className="ml-auto" />
    </div>
  )
}

export function Component() {
  const canManage = useHasPerm('tours:manage')
  const tours = useTours()
  const create = useCreateTour()
  const navigate = useNavigate()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [name, setName] = useState('')

  async function submit() {
    if (!name.trim()) return
    try {
      const tour = await create.mutateAsync({ name: name.trim(), description: '' })
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
            Guide users through your product with in-app step-by-step tours.
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
          {tours.isLoading ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-40 w-full" />
              ))}
            </div>
          ) : tours.isError ? (
            <Card className="p-6 text-center text-sm text-muted-foreground">
              Could not load tours.{' '}
              <Button variant="link" className="px-1" onClick={() => tours.refetch()}>
                Retry
              </Button>
            </Card>
          ) : !tours.data || tours.data.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <MapIcon />
                </EmptyMedia>
                <EmptyTitle>No tours yet</EmptyTitle>
                <EmptyDescription>
                  Build a guided walkthrough, or record one from your live app.
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
              {tours.data.map((tour) => (
                <Card
                  key={tour.id}
                  role="button"
                  tabIndex={0}
                  className="cursor-pointer transition-colors hover:border-primary/40"
                  onClick={() => navigate(`/tours/${tour.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') navigate(`/tours/${tour.id}`)
                  }}
                >
                  <CardHeader>
                    <div className="flex items-center justify-between gap-2">
                      <CardTitle className="truncate">{tour.name}</CardTitle>
                      <TourStatusBadge status={tour.status} />
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {tour.steps.length} step{tour.steps.length === 1 ? '' : 's'}
                      {tour.description ? ` · ${tour.description}` : ''}
                    </p>
                  </CardHeader>
                  <CardContent>
                    <TourStatsRow tour={tour} />
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>New tour</DialogTitle>
            <DialogDescription>Give your tour a name to start adding steps.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2 py-2">
            <Label htmlFor="tour-name">Name</Label>
            <Input
              id="tour-name"
              value={name}
              placeholder="Onboarding walkthrough"
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submit()
              }}
            />
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
