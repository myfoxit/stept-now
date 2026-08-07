import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

import type { ReportByAgent } from '../api'
import { formatMinutes } from '../lib'

export function ByAgentTable({ data }: { data: ReportByAgent[] }) {
  const max = Math.max(1, ...data.map((row) => row.resolved))
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">By agent</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No agent activity yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Agent</TableHead>
                  <TableHead className="w-1/2">Resolved</TableHead>
                  <TableHead className="text-right">Median first response</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((row) => (
                  <TableRow key={row.user_id}>
                    <TableCell className="font-medium">{row.name}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-brand/60"
                            style={{ width: `${(row.resolved / max) * 100}%` }}
                          />
                        </div>
                        <span className="w-8 text-right text-sm tabular-nums">{row.resolved}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatMinutes(row.median_first_response_minutes)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
