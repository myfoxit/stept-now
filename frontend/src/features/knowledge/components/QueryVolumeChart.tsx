import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from 'recharts'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'

import type { AnalyticsQueriesPerDay } from '../api'
import { shortDay } from '../lib'
import { t } from '@/i18n'

// Categorical slot 1 (blue) from the validated dataviz palette, stepped per mode.
// Single series ⇒ no legend box — the card title names it (dataviz).
const chartConfig = {
  count: { label: 'Queries', theme: { light: '#2a78d6', dark: '#3987e5' } },
} satisfies ChartConfig

export function QueryVolumeChart({ data }: { data: AnalyticsQueriesPerDay[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t('knowledge.queries_over_time')}</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">{t('common.no_data_for_this_range')}</p>
        ) : (
          <ChartContainer config={chartConfig} className="aspect-auto h-64 w-full">
            <AreaChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
              <defs>
                <linearGradient id="fill-queries" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-count)" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="var(--color-count)" stopOpacity={0.02} />
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
              <Area
                dataKey="count"
                name="count"
                type="monotone"
                stroke="var(--color-count)"
                strokeWidth={2}
                fill="url(#fill-queries)"
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
