import { AlertTriangle } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { messageTime } from '@/lib/format'
import { cn } from '@/lib/utils'

import type { TourEvent } from '../api'
import { useLiveTourEvents, useTourEvents } from '../hooks'
import { eventLabel } from '../lib'

export const EVENTS_PAGE_SIZE = 25

const EVENT_STYLES: Record<string, string> = {
  completed: 'border-transparent bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
  step_error: 'border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-400',
}

function MetaCell({ event }: { event: TourEvent }) {
  const meta = (event.meta ?? {}) as Record<string, unknown>
  const url = typeof meta.url === 'string' ? meta.url : null
  const reason = typeof meta.reason === 'string' ? meta.reason : null
  return (
    <div className="flex max-w-xs items-center gap-2">
      {meta.healed === true ? (
        <Badge
          variant="secondary"
          className="gap-1 border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-400"
        >
          <AlertTriangle aria-hidden /> healed
        </Badge>
      ) : null}
      {reason ? <span className="text-xs text-destructive">{reason}</span> : null}
      {url ? (
        <span className="truncate font-mono text-xs text-muted-foreground" title={url}>
          {url}
        </span>
      ) : null}
      {!url && !reason && meta.healed !== true ? (
        <span className="text-xs text-muted-foreground">—</span>
      ) : null}
    </div>
  )
}

/** Newest-first telemetry feed; page 1 grows live from the `tour.event` topic. */
export function TourEventsTable({ tourId }: { tourId: string }) {
  const [offset, setOffset] = useState(0)
  const events = useTourEvents(tourId, { limit: EVENTS_PAGE_SIZE, offset })
  useLiveTourEvents(tourId, EVENTS_PAGE_SIZE)

  const page = events.data
  const total = page?.total ?? 0
  const shown = page?.items.length ?? 0

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Recent events</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        {events.isLoading ? (
          <div className="grid gap-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : events.isError ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Could not load events.{' '}
            <Button variant="link" className="px-1" onClick={() => events.refetch()}>
              Retry
            </Button>
          </p>
        ) : shown === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No events yet — they land here as soon as someone sees this tour.
          </p>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Event</TableHead>
                  <TableHead>Step</TableHead>
                  <TableHead>Contact</TableHead>
                  <TableHead>Details</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page!.items.map((event) => (
                  <TableRow key={event.id} data-testid="event-row">
                    <TableCell className="text-xs text-muted-foreground">
                      {messageTime(event.created_at)}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className={cn(EVENT_STYLES[event.event])}>
                        {eventLabel(event.event)}
                      </Badge>
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {event.step_index == null ? '—' : event.step_index + 1}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {event.contact_id ? event.contact_id.slice(0, 8) : 'anonymous'}
                    </TableCell>
                    <TableCell>
                      <MetaCell event={event} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {offset + 1}–{offset + shown} of {total}
              </span>
              <div className="flex-1" />
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - EVENTS_PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + shown >= total}
                onClick={() => setOffset(offset + EVENTS_PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
