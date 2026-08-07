import { AlertTriangle } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

import type { TourStepStat } from '../api'

/**
 * Per-step funnel. Bar length is the step's share of the widest step; the
 * lighter tail is the drop-off. `healed` counts playbacks where the primary
 * selector missed and the player had to fall back — a step that keeps healing
 * is one deploy away from breaking, so it is called out as a warning.
 */
export function StepFunnel({ steps }: { steps: TourStepStat[] }) {
  const max = Math.max(1, ...steps.map((step) => step.viewed))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Step funnel</CardTitle>
      </CardHeader>
      <CardContent>
        {steps.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            This tour has no steps yet.
          </p>
        ) : (
          <ul className="grid gap-3">
            {steps.map((step) => {
              const retained = Math.max(step.viewed - step.drop_off, 0)
              return (
                <li key={step.index} className="grid gap-1" data-testid="funnel-step">
                  <div className="flex flex-wrap items-baseline gap-2 text-sm">
                    <span className="font-medium">
                      {step.index + 1}. {step.title || 'Untitled step'}
                    </span>
                    <div className="flex-1" />
                    <span className="tabular-nums text-muted-foreground">
                      {step.viewed} viewed · {step.drop_off} dropped
                    </span>
                    {step.healed > 0 ? (
                      <Badge
                        variant="secondary"
                        className="gap-1 border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-400"
                      >
                        <AlertTriangle aria-hidden />
                        {step.healed} healed
                      </Badge>
                    ) : null}
                  </div>
                  <div
                    className="h-2.5 w-full overflow-hidden rounded-full bg-muted"
                    role="img"
                    aria-label={`Step ${step.index + 1}: ${step.viewed} viewed, ${step.drop_off} dropped off, ${step.healed} healed`}
                  >
                    {/* Widths are data — the one thing tokens cannot express. */}
                    <div className="flex h-full" style={{ width: `${(step.viewed / max) * 100}%` }}>
                      <div
                        className="h-full bg-brand/70"
                        style={{
                          width: step.viewed ? `${(retained / step.viewed) * 100}%` : '0%',
                        }}
                      />
                      <div
                        className="h-full bg-muted-foreground/30"
                        style={{
                          width: step.viewed ? `${(step.drop_off / step.viewed) * 100}%` : '0%',
                        }}
                      />
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
