/**
 * Per-dimension breakdown with drill-down and CSV export.
 *
 * Each row carries the filter document that reproduces its own population, so
 * clicking a number opens exactly those conversations — the number and the list
 * can't drift apart because they come from the same query.
 */

import { Download } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { authHeaders } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useDrilldownStore } from '@/stores/drilldown'
import type { FilterQuery } from '@/features/inbox/api'
import { DIMENSIONS, reportsExtraApi, type ReportDimensionRow } from '@/features/reports/api'
import { useBreakdown } from '@/features/reports/hooks'

async function downloadCsv(url: string, filename: string) {
  const response = await fetch(url, { headers: authHeaders() })
  if (!response.ok) return
  const objectUrl = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  link.click()
  URL.revokeObjectURL(objectUrl)
}

function minutes(value: number | null | undefined): string {
  if (value == null) return '—'
  if (value >= 60) return `${(value / 60).toFixed(1)}h`
  return `${Math.round(value)}m`
}

export function BreakdownTable({ days }: { days: number }) {
  const [dimension, setDimension] = useState('agent')
  const { data, isLoading } = useBreakdown(dimension, days)
  const navigate = useNavigate()
  const setDrilldown = useDrilldownStore((s) => s.set)

  function drill(row: ReportDimensionRow) {
    // Hand the row's own filter to the inbox, which runs it via
    // POST /conversations/search — same query, same population.
    // The API types `filter` as an open JSON object; it is always a filter
    // document, and the search endpoint validates it either way.
    setDrilldown({
      label: `${row.label} · last ${days}d`,
      query: row.filter as unknown as FilterQuery,
    })
    navigate('/inbox')
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <CardTitle className="text-base">Breakdown</CardTitle>
        <Select value={dimension} onValueChange={setDimension}>
          <SelectTrigger className="ml-2 h-8 w-32" aria-label="Group by">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {DIMENSIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          onClick={() =>
            downloadCsv(
              reportsExtraApi.csvUrl('breakdown', { dimension, days }),
              `${dimension}-${days}d.csv`
            )
          }
        >
          <Download className="mr-1 size-4" />
          CSV
        </Button>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : (data?.rows.length ?? 0) === 0 ? (
          <p className="text-sm text-muted-foreground">No conversations in this window.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="capitalize">{dimension}</TableHead>
                <TableHead className="text-right">New</TableHead>
                <TableHead className="text-right">Resolved</TableHead>
                <TableHead className="text-right">Resolution rate</TableHead>
                <TableHead className="text-right">Median first reply</TableHead>
                <TableHead className="text-right">Median resolution</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.rows.map((row) => (
                <TableRow key={`${row.key}:${row.label}`}>
                  <TableCell className="font-medium">{row.label}</TableCell>
                  <TableCell className="text-right">
                    <button
                      type="button"
                      className="underline-offset-2 hover:underline"
                      onClick={() => drill(row)}
                      aria-label={`Show the ${row.new} conversations for ${row.label}`}
                    >
                      {row.new}
                    </button>
                  </TableCell>
                  <TableCell className="text-right">{row.resolved}</TableCell>
                  <TableCell className="text-right">
                    {Math.round((row.resolution_rate ?? 0) * 100)}%
                  </TableCell>
                  <TableCell className="text-right">
                    {minutes(row.median_first_response_minutes)}
                  </TableCell>
                  <TableCell className="text-right">
                    {minutes(row.median_resolution_minutes)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
