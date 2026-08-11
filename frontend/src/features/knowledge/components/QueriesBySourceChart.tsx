import { Bar, BarChart, CartesianGrid, LabelList, XAxis } from 'recharts'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'

import type { AnalyticsQueriesBySource } from '../api'
import { t } from '@/i18n'

// Single measure across categories ⇒ one hue, no legend (axis names the category).
const chartConfig = {
  count: { label: 'Queries', theme: { light: '#2a78d6', dark: '#3987e5' } },
} satisfies ChartConfig

export function QueriesBySourceChart({ data }: { data: AnalyticsQueriesBySource[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t('knowledge.by_source')}</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">{t('common.no_data_for_this_range')}</p>
        ) : (
          <ChartContainer config={chartConfig} className="aspect-auto h-64 w-full">
            <BarChart data={data} margin={{ top: 20, left: 4, right: 4 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="source" tickLine={false} axisLine={false} tickMargin={8} />
              <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel />} />
              <Bar dataKey="count" fill="var(--color-count)" radius={[4, 4, 0, 0]} maxBarSize={24}>
                <LabelList
                  dataKey="count"
                  position="top"
                  offset={8}
                  className="fill-muted-foreground"
                  fontSize={12}
                />
              </Bar>
            </BarChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  )
}
