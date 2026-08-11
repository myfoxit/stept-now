import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from 'recharts'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'

import type { SurveyDayPoint } from '../api'
import { shortDay } from '../lib'
import { t } from '@/i18n'

// One series ⇒ one hue (categorical slot 1, stepped per mode) and no legend —
// the card title names the measure. Same palette as the reports dashboard.
const chartConfig = {
  responses: { label: 'Responses', theme: { light: '#2a78d6', dark: '#3987e5' } },
} satisfies ChartConfig

export function ResponsesByDayChart({ data }: { data: SurveyDayPoint[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t('surveys.responses_over_time')}</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">{t('surveys.no_responses_yet')}</p>
        ) : (
          <ChartContainer config={chartConfig} className="aspect-auto h-56 w-full">
            <AreaChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
              <defs>
                <linearGradient id="fill-survey-responses" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-responses)" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="var(--color-responses)" stopOpacity={0.02} />
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
                dataKey="responses"
                name="responses"
                type="monotone"
                stroke="var(--color-responses)"
                strokeWidth={2}
                fill="url(#fill-survey-responses)"
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
