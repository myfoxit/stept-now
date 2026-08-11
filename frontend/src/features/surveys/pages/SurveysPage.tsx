import { BarChart3, MessageSquareHeart, Pause, Play, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { useHasPerm } from '@/stores/auth'

import type { Survey } from '../api'
import { SurveyStatusBadge } from '../components/SurveyStatusBadge'
import {
  useCreateSurvey,
  useDeleteSurvey,
  usePauseSurvey,
  usePublishSurvey,
  useSurveyResults,
  useSurveys,
} from '../hooks'
import { describeTrigger, formatRate } from '../lib'
import { t } from '@/i18n'

function SurveyResponseCount({ survey }: { survey: Survey }) {
  const results = useSurveyResults(survey.id, survey.status !== 'draft')

  if (survey.status === 'draft') {
    return <p className="text-xs text-muted-foreground">{t('common.not_published_yet')}</p>
  }
  if (results.isLoading) return <Skeleton className="h-8 w-32" />
  if (!results.data) return <p className="text-xs text-muted-foreground">{t('common.no_data')}</p>

  return (
    <div className="flex items-center gap-5">
      <div>
        <div className="text-sm font-semibold tabular-nums">{results.data.responses}</div>
        <div className="text-[11px] text-muted-foreground">{t('surveys.responses_2')}</div>
      </div>
      <div>
        <div className="text-sm font-semibold tabular-nums">{results.data.completed}</div>
        <div className="text-[11px] text-muted-foreground">{t('common.completed')}</div>
      </div>
      <div>
        <div className="text-sm font-semibold tabular-nums">
          {formatRate(results.data.completion_rate)}
        </div>
        <div className="text-[11px] text-muted-foreground">{t('common.completion_rate_2')}</div>
      </div>
    </div>
  )
}

function SurveyCard({
  survey,
  canManage,
  onDelete,
}: {
  survey: Survey
  canManage: boolean
  onDelete: (survey: Survey) => void
}) {
  const publish = usePublishSurvey()
  const pause = usePauseSurvey()

  return (
    <Card className="gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Link
          to={`/surveys/${survey.id}`}
          className="truncate font-medium hover:underline focus-visible:underline"
        >
          {survey.name}
        </Link>
        <SurveyStatusBadge status={survey.status} />
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          asChild
          aria-label={`Results for ${survey.name}`}
        >
          <Link to={`/surveys/${survey.id}/results`}>
            <BarChart3 className="size-4" />
          </Link>
        </Button>
        {canManage ? (
          <>
            {survey.status === 'live' ? (
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label={`Pause ${survey.name}`}
                disabled={pause.isPending}
                onClick={() => pause.mutate(survey.id)}
              >
                <Pause className="size-4" />
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label={`Publish ${survey.name}`}
                disabled={publish.isPending}
                onClick={() => publish.mutate(survey.id)}
              >
                <Play className="size-4" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              aria-label={`Delete ${survey.name}`}
              onClick={() => onDelete(survey)}
            >
              <Trash2 className="size-4" />
            </Button>
          </>
        ) : null}
      </div>

      <p className="text-xs text-muted-foreground">
        {survey.questions.length} question{survey.questions.length === 1 ? '' : 's'} ·{' '}
        {survey.presentation} · {describeTrigger(survey)}
      </p>

      <SurveyResponseCount survey={survey} />
    </Card>
  )
}

export function Component() {
  const canRead = useHasPerm('tours:read')
  const canManage = useHasPerm('tours:manage')
  const surveys = useSurveys(canRead)
  const create = useCreateSurvey()
  const remove = useDeleteSurvey()
  const navigate = useNavigate()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [name, setName] = useState('')
  const [deleting, setDeleting] = useState<Survey | null>(null)

  async function submit() {
    if (!name.trim()) return
    try {
      const survey = await create.mutateAsync({ name: name.trim() })
      setDialogOpen(false)
      setName('')
      navigate(`/surveys/${survey.id}`)
    } catch {
      /* toast handled in hook */
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-4 border-b px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold">{t('surveys.surveys')}</h1>
          <p className="text-sm text-muted-foreground">
            {t('surveys.ask_for_nps_ratings_or_open')}
          </p>
        </div>
        {canManage ? (
          <Button onClick={() => setDialogOpen(true)}>
            <Plus className="size-4" /> {t('surveys.new_survey')}
          </Button>
        ) : null}
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-4xl">
          {!canRead ? (
            <p className="rounded-md border p-6 text-center text-sm text-muted-foreground">
              You don&rsquo;t have access to surveys.
            </p>
          ) : surveys.isLoading ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-36 w-full" />
              ))}
            </div>
          ) : surveys.isError ? (
            <Card className="p-6 text-center text-sm text-muted-foreground">
              Could not load surveys.{' '}
              <Button variant="link" className="px-1" onClick={() => surveys.refetch()}>
                {t('common.retry')}
              </Button>
            </Card>
          ) : !surveys.data || surveys.data.length === 0 ? (
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <MessageSquareHeart />
                </EmptyMedia>
                <EmptyTitle>{t('surveys.no_surveys_yet')}</EmptyTitle>
                <EmptyDescription>
                  {t('surveys.start_with_an_nps_question_then')}
                </EmptyDescription>
              </EmptyHeader>
              {canManage ? (
                <EmptyContent>
                  <Button onClick={() => setDialogOpen(true)}>
                    <Plus className="size-4" /> {t('surveys.create_your_first_survey')}
                  </Button>
                </EmptyContent>
              ) : null}
            </Empty>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2">
              {surveys.data.map((survey) => (
                <li key={survey.id}>
                  <SurveyCard survey={survey} canManage={canManage} onDelete={setDeleting} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('surveys.new_survey')}</DialogTitle>
            <DialogDescription>
              Name it now — you&rsquo;ll add questions and targeting in the editor.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor="survey-new-name">{t('common.name')}</Label>
            <Input
              id="survey-new-name"
              placeholder={t('surveys.how_are_we_doing')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submit()
              }}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button onClick={submit} disabled={!name.trim() || create.isPending}>
              {create.isPending ? 'Creating…' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{deleting?.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              {t('surveys.the_survey_stops_showing_and_every')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleting) remove.mutate(deleting.id)
                setDeleting(null)
              }}
            >
              {t('common.delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

export default Component
