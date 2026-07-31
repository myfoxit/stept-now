import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from 'recharts'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'

import type { ReportByDay } from '../api'
import { shortDay } from '../lib'

// Categorical slots 1 (blue) + 3 (aqua) from the validated dataviz palette,
// stepped per mode. Two series ⇒ legend is always present (identity never color-alone).
const chartConfig = {
  new: { label: 'New', theme: { light: '#2a78d6', dark: '#3987e5' } },
  resolved: { label: 'Resolved', theme: { light: '#1baf7a', dark: '#199e70' } },
} satisfies ChartConfig

export function ByDayChart({ data }: { data: ReportByDay[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Conversations over time</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">No data for this range.</p>
        ) : (
          <ChartContainer config={chartConfig} className="aspect-auto h-64 w-full">
            <AreaChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
              <defs>
                <linearGradient id="fill-new" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-new)" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="var(--color-new)" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="fill-resolved" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-resolved)" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="var(--color-resolved)" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} />
              <XAxis
                dataKey="date"
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                minTickGap={24}
                tickFormatter={shortDay}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                width={32}
                allowDecimals={false}
                tickMargin={4}
              />
              <ChartTooltip
                content={<ChartTooltipContent labelFormatter={(value) => shortDay(String(value))} />}
              />
              <ChartLegend content={<ChartLegendContent />} />
              <Area
                dataKey="new"
                name="new"
                type="monotone"
                stroke="var(--color-new)"
                strokeWidth={2}
                fill="url(#fill-new)"
                dot={false}
                activeDot={{ r: 4 }}
              />
              <Area
                dataKey="resolved"
                name="resolved"
                type="monotone"
                stroke="var(--color-resolved)"
                strokeWidth={2}
                fill="url(#fill-resolved)"
                dot={false}
                activeDot={{ r: 4 }}
              />
            </AreaChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  )
}
