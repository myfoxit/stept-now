import { ArrowLeft, Pencil } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useHasPerm } from '@/stores/auth'

import { DistributionBars } from '../components/DistributionBars'
import { NpsCard } from '../components/NpsCard'
import { ResponsesByDayChart } from '../components/ResponsesByDayChart'
import { SurveyStatusBadge } from '../components/SurveyStatusBadge'
import { TextAnswers } from '../components/TextAnswers'
import { useLiveSurveyResults, useSurvey, useSurveyResults } from '../hooks'
import { formatRate, ratingRows, selectRows } from '../lib'
import { t } from '@/i18n'

function StatTile({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <Card className="gap-1 p-4">
      <div className="text-sm text-muted-foreground">{label}</div>
      {/* Proportional figures for large standalone values (dataviz). */}
      <div className="text-2xl font-semibold">{value}</div>
      {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
    </Card>
  )
}

export function Component() {
  const { surveyId } = useParams<{ surveyId: string }>()
  const canRead = useHasPerm('tours:read')
  const canManage = useHasPerm('tours:manage')

  const surveyQuery = useSurvey(canRead ? surveyId : undefined)
  const results = useSurveyResults(surveyId, canRead)
  // Live-append: a submitted response invalidates results + the responses pages.
  useLiveSurveyResults(surveyId)

  if (!canRead) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card className="p-6 text-center text-sm text-muted-foreground">
          You don&rsquo;t have access to surveys.
        </Card>
      </div>
    )
  }

  const survey = surveyQuery.data

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b px-6 py-4">
        <Button variant="ghost" size="icon" asChild aria-label={t('surveys.back_to_surveys')}>
          <Link to="/surveys">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-lg font-semibold">{survey?.name ?? 'Survey results'}</h1>
            {survey ? <SurveyStatusBadge status={survey.status} /> : null}
          </div>
          <p className="text-sm text-muted-foreground">{t('surveys.live_responses_and_score_breakdown')}</p>
        </div>
        {canManage && surveyId ? (
          <Button variant="outline" size="sm" asChild>
            <Link to={`/surveys/${surveyId}`}>
              <Pencil className="size-4" /> {t('surveys.edit_survey')}
            </Link>
          </Button>
        ) : null}
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto grid max-w-5xl gap-4">
          {results.isLoading ? (
            <>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-24 w-full" />
                ))}
              </div>
              <Skeleton className="h-56 w-full" />
              <Skeleton className="h-56 w-full" />
            </>
          ) : results.isError ? (
            <Card className="p-6 text-center text-sm text-muted-foreground">
              Could not load results.{' '}
              <Button variant="link" className="px-1" onClick={() => results.refetch()}>
                {t('common.retry')}
              </Button>
            </Card>
          ) : results.data ? (
            <>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
                <StatTile label={t('surveys.responses')} value={results.data.responses} />
                <StatTile label={t('common.completed_2')} value={results.data.completed} />
                <StatTile
                  label={t('common.completion_rate')}
                  value={formatRate(results.data.completion_rate)}
                  hint={`${results.data.completed} of ${results.data.responses} finished`}
                />
              </div>

              {results.data.nps ? <NpsCard nps={results.data.nps} /> : null}

              <div className="grid gap-4 lg:grid-cols-2">
                {results.data.ratings ? (
                  <DistributionBars
                    title={t('surveys.rating_distribution')}
                    description={`Average ${results.data.ratings.avg.toFixed(1)} out of 5`}
                    rows={ratingRows(results.data.ratings.distribution).map((row) => ({
                      label: `${row.rating} star${row.rating === '1' ? '' : 's'}`,
                      count: row.count,
                    }))}
                  />
                ) : null}

                {(results.data.select ?? []).map((question) => (
                  <DistributionBars
                    key={question.question_id}
                    title={question.question}
                    rows={selectRows(question.counts).map((row) => ({
                      label: row.option,
                      count: row.count,
                    }))}
                  />
                ))}
              </div>

              <ResponsesByDayChart data={results.data.by_day} />

              {surveyId ? (
                <TextAnswers surveyId={surveyId} questions={survey?.questions ?? []} />
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export default Component
