/**
 * SLA attainment per policy.
 *
 * Reads the `sla_events` the breach scan already records, so "did we hit our
 * targets" and "which target did we miss" come from the same rows that fired
 * the notifications — not from a second, separately-drifting calculation.
 */

import { Download } from 'lucide-react'

import { authHeaders } from '@/api/client'
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
import { reportsExtraApi } from '@/features/reports/api'
import { useSlaReport } from '@/features/reports/hooks'

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

export function SlaAttainmentTable({ days }: { days: number }) {
  const { data, isLoading } = useSlaReport(days)
  const rows = data?.by_policy ?? []

  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <CardTitle className="text-base">SLA attainment</CardTitle>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          onClick={() => downloadCsv(reportsExtraApi.csvUrl('sla', { days }), `sla-${days}d.csv`)}
        >
          <Download className="mr-1 size-4" />
          CSV
        </Button>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No SLA policies were applied in this window.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Policy</TableHead>
                <TableHead className="text-right">Applied</TableHead>
                <TableHead className="text-right">Hit</TableHead>
                <TableHead className="text-right">Missed</TableHead>
                <TableHead className="text-right">Attainment</TableHead>
                <TableHead className="text-right">FRT / NRT / RT misses</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.sla_policy_id}>
                  <TableCell className="font-medium">{row.name}</TableCell>
                  <TableCell className="text-right">{row.applied}</TableCell>
                  <TableCell className="text-right">{row.hit}</TableCell>
                  <TableCell className="text-right">{row.missed}</TableCell>
                  <TableCell className="text-right">
                    {Math.round((row.attainment_rate ?? 0) * 100)}%
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground">
                    {row.frt_breaches} / {row.nrt_breaches} / {row.rt_breaches}
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
