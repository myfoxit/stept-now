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
import { t } from '@/i18n'

export function TopQueriesTable({ data }: { data: AnalyticsTopQuery[] }) {
  const max = Math.max(1, ...data.map((row) => row.count))
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t('knowledge.top_queries')}</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">{t('knowledge.no_queries_yet')}</p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('knowledge.query')}</TableHead>
                  <TableHead className="w-1/3">{t('knowledge.count')}</TableHead>
                  <TableHead className="text-right">{t('knowledge.avg_score')}</TableHead>
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
                            className="h-full rounded-full bg-brand/60"
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
        <CardTitle className="text-sm">{t('knowledge.content_gaps')}</CardTitle>
        <CardDescription>
          {t('knowledge.questions_your_knowledge_base_couldn_t')}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {t('knowledge.no_unanswered_queries_your_knowledge_base')}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('knowledge.query')}</TableHead>
                  <TableHead className="w-24 text-right">{t('knowledge.count')}</TableHead>
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
