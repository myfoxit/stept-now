import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'

export interface DistributionRow {
  label: string
  count: number
}

/**
 * One measure (a count) across labelled buckets — a bar table rather than a
 * chart: one hue (the brand design token, so dark mode follows the theme), no
 * legend (the row label names the category), and every value printed in ink.
 */
export function DistributionBars({
  title,
  description,
  rows,
  emptyLabel = 'No answers yet.',
}: {
  title: string
  description?: string
  rows: DistributionRow[]
  emptyLabel?: string
}) {
  const total = rows.reduce((sum, row) => sum + row.count, 0)
  const max = rows.reduce((best, row) => Math.max(best, row.count), 0)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
        {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">{emptyLabel}</p>
        ) : (
          <table className="w-full text-sm" aria-label={title}>
            <tbody>
              {rows.map((row) => (
                <tr key={row.label}>
                  <th
                    scope="row"
                    className="w-28 max-w-28 truncate py-1 pr-3 text-left font-normal text-muted-foreground"
                  >
                    {row.label}
                  </th>
                  <td className="py-1">
                    <Progress
                      className="h-2.5"
                      value={max === 0 ? 0 : Math.round((row.count / max) * 100)}
                      aria-label={`${row.label}: ${row.count}`}
                    />
                  </td>
                  <td className="w-10 py-1 pl-3 text-right tabular-nums">{row.count}</td>
                  <td className="w-14 py-1 pl-2 text-right tabular-nums text-muted-foreground">
                    {Math.round((row.count / total) * 100)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  )
}
