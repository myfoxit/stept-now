import { ArrowLeft, Pencil } from 'lucide-react'
import { Link, useParams } from 'react-router'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useHasPerm } from '@/stores/auth'

import { StepFunnel } from '../components/StepFunnel'
import { TourByDayChart } from '../components/TourByDayChart'
import { TourEventsTable } from '../components/TourEventsTable'
import { TourKindBadge } from '../components/TourBadges'
import { TourKpiTiles } from '../components/TourKpiTiles'
import { TourStatusBadge } from '../components/TourStatusBadge'
import { useTour, useTourStats } from '../hooks'

export function Component() {
  const { tourId } = useParams<{ tourId: string }>()
  const canRead = useHasPerm('tours:read')
  const tourQuery = useTour(canRead ? tourId : undefined)
  const statsQuery = useTourStats(tourId, canRead)

  if (!canRead) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card className="p-6 text-center text-sm text-muted-foreground">
          You don’t have access to tour analytics.
        </Card>
      </div>
    )
  }

  const tour = tourQuery.data

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b px-6 py-4">
        <Button variant="ghost" size="icon" asChild aria-label="Back to tours">
          <Link to="/tours">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-lg font-semibold">{tour?.name ?? 'Tour analytics'}</h1>
            {tour ? <TourStatusBadge status={tour.status} /> : null}
            {tour ? <TourKindBadge kind={tour.kind} /> : null}
          </div>
          <p className="text-sm text-muted-foreground">
            Delivery, drop-off and self-healing for this experience.
          </p>
        </div>
        {tourId ? (
          <Button variant="outline" size="sm" asChild>
            <Link to={`/tours/${tourId}`}>
              <Pencil className="size-4" /> Edit tour
            </Link>
          </Button>
        ) : null}
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto grid max-w-5xl gap-4">
          {statsQuery.isLoading ? (
            <>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                {[0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-24 w-full" />
                ))}
              </div>
              <Skeleton className="h-48 w-full" />
              <Skeleton className="h-64 w-full" />
            </>
          ) : statsQuery.isError ? (
            <Card className="p-6 text-center text-sm text-muted-foreground">
              Could not load analytics for this tour.{' '}
              <Button variant="link" className="px-1" onClick={() => statsQuery.refetch()}>
                Retry
              </Button>
            </Card>
          ) : statsQuery.data ? (
            <>
              <TourKpiTiles stats={statsQuery.data} />
              <StepFunnel steps={statsQuery.data.steps} />
              <TourByDayChart data={statsQuery.data.by_day} />
              {tourId ? <TourEventsTable tourId={tourId} /> : null}
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export default Component
