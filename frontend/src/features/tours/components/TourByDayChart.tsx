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

import type { TourDayStat } from '../api'
import { byDaySeries, isEmptySeries, shortDay } from '../lib'

// Categorical slots 1 (blue) + 3 (aqua) from the validated dataviz palette,
// stepped per mode — same two series colors the reports dashboard uses.
const chartConfig = {
  starts: { label: 'Starts', theme: { light: '#2a78d6', dark: '#3987e5' } },
  completions: { label: 'Completions', theme: { light: '#1baf7a', dark: '#199e70' } },
} satisfies ChartConfig

export function TourByDayChart({ data }: { data: TourDayStat[] | undefined }) {
  const series = byDaySeries(data)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Starts vs completions</CardTitle>
      </CardHeader>
      <CardContent>
        {isEmptySeries(data) ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            No activity in the last 30 days.
          </p>
        ) : (
          <ChartContainer config={chartConfig} className="aspect-auto h-64 w-full">
            <AreaChart data={series} margin={{ left: 4, right: 12, top: 8 }}>
              <defs>
                <linearGradient id="fill-tour-starts" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-starts)" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="var(--color-starts)" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="fill-tour-completions" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-completions)" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="var(--color-completions)" stopOpacity={0.02} />
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
                content={
                  <ChartTooltipContent labelFormatter={(value) => shortDay(String(value))} />
                }
              />
              <ChartLegend content={<ChartLegendContent />} />
              <Area
                dataKey="starts"
                name="starts"
                type="monotone"
                stroke="var(--color-starts)"
                strokeWidth={2}
                fill="url(#fill-tour-starts)"
                dot={false}
                activeDot={{ r: 4 }}
              />
              <Area
                dataKey="completions"
                name="completions"
                type="monotone"
                stroke="var(--color-completions)"
                strokeWidth={2}
                fill="url(#fill-tour-completions)"
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
