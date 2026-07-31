import { Bar, BarChart, CartesianGrid, LabelList, XAxis } from 'recharts'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'

import type { ReportByChannel } from '../api'
import { channelLabel } from '../lib'

// Single measure across categories ⇒ one hue, no legend (axis names the category).
const chartConfig = {
  count: { label: 'Conversations', theme: { light: '#2a78d6', dark: '#3987e5' } },
} satisfies ChartConfig

export function ByChannelChart({ data }: { data: ReportByChannel[] }) {
  const rows = data.map((row) => ({ channel: channelLabel(row.channel_type), count: row.count }))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">By channel</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">No data for this range.</p>
        ) : (
          <ChartContainer config={chartConfig} className="aspect-auto h-64 w-full">
            <BarChart data={rows} margin={{ top: 20, left: 4, right: 4 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="channel" tickLine={false} axisLine={false} tickMargin={8} />
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
