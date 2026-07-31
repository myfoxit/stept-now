import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

import type { AnalyticsTopQuery, AnalyticsZeroResultQuery } from '../api'
import { formatScore } from '../lib'

export function TopQueriesTable({ data }: { data: AnalyticsTopQuery[] }) {
  const max = Math.max(1, ...data.map((row) => row.count))
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Top queries</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No queries yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Query</TableHead>
                  <TableHead className="w-1/3">Count</TableHead>
                  <TableHead className="text-right">Avg score</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((row) => (
                  <TableRow key={row.query}>
                    <TableCell className="max-w-xs truncate font-medium">{row.query}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-primary/60"
                            style={{ width: `${(row.count / max) * 100}%` }}
                          />
                        </div>
                        <span className="w-8 text-right text-sm tabular-nums">{row.count}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatScore(row.avg_top_score)}
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

export function ContentGapsTable({ data }: { data: AnalyticsZeroResultQuery[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Content gaps</CardTitle>
        <CardDescription>
          Questions your knowledge base couldn’t answer — add content for these.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No unanswered queries — your knowledge base is covering everything asked.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Query</TableHead>
                  <TableHead className="w-24 text-right">Count</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((row) => (
                  <TableRow key={row.query}>
                    <TableCell className="max-w-md truncate font-medium">{row.query}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.count}</TableCell>
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
