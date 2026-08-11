import { Bar, BarChart, XAxis, YAxis } from 'recharts'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'

import type { SurveyNpsResult } from '../api'
import { npsSegments } from '../lib'
import { t } from '@/i18n'

/**
 * Promoters / passives / detractors read as polarity, so the three fills are
 * categorical slots 1–3 of the validated palette (aqua / blue / orange), which
 * clear every all-pairs CVD and normal-vision gate in both modes. Slot-3 aqua
 * sits under 3:1 on the light surface, so the legend names each segment and the
 * counts are ALSO printed as text (relief rule) — identity is never colour-alone.
 */
const chartConfig = {
  promoters: { label: 'Promoters', theme: { light: '#1baf7a', dark: '#199e70' } },
  passives: { label: 'Passives', theme: { light: '#2a78d6', dark: '#3987e5' } },
  detractors: { label: 'Detractors', theme: { light: '#eb6834', dark: '#d95926' } },
} satisfies ChartConfig

export function NpsCard({ nps }: { nps: SurveyNpsResult }) {
  const { total, segments } = npsSegments(nps)
  const row = {
    name: 'nps',
    promoters: nps.promoters,
    passives: nps.passives,
    detractors: nps.detractors,
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{t('surveys.net_promoter_score')}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="flex items-baseline gap-3">
          <span className="text-5xl font-semibold tabular-nums">{nps.score}</span>
          <span className="text-xs text-muted-foreground">
            on a −100 to 100 scale · {total} scored response{total === 1 ? '' : 's'}
          </span>
        </div>

        {total === 0 ? (
          <p className="text-sm text-muted-foreground">{t('surveys.no_scored_responses_yet')}</p>
        ) : (
          <>
            <ChartContainer config={chartConfig} className="aspect-auto h-20 w-full">
              <BarChart layout="vertical" data={[row]} stackOffset="expand" barCategoryGap={0}>
                <XAxis type="number" hide domain={[0, 1]} />
                <YAxis type="category" dataKey="name" hide />
                <ChartTooltip content={<ChartTooltipContent hideLabel />} />
                <ChartLegend content={<ChartLegendContent />} />
                <Bar
                  dataKey="promoters"
                  stackId="nps"
                  fill="var(--color-promoters)"
                  radius={[4, 0, 0, 4]}
                />
                <Bar dataKey="passives" stackId="nps" fill="var(--color-passives)" />
                <Bar
                  dataKey="detractors"
                  stackId="nps"
                  fill="var(--color-detractors)"
                  radius={[0, 4, 4, 0]}
                />
              </BarChart>
            </ChartContainer>

            {/* Table view: every segment named and numbered in ink, never colour alone. */}
            <ul className="grid gap-1.5 text-sm">
              {segments.map((segment) => (
                <li key={segment.key} className="flex items-center gap-2">
                  <span className="text-muted-foreground">{segment.label}</span>
                  <span className="flex-1" />
                  <span className="tabular-nums">{segment.count}</span>
                  <span className="w-16 text-right tabular-nums text-muted-foreground">
                    {segment.percent}%
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  )
}
