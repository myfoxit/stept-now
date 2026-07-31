import { ArrowLeft, BarChart3, PauseCircle, PlayCircle, Save, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'

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
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { useHasPerm } from '@/stores/auth'

import type { FrequencyType, SurveyPresentation } from '../api'
import { AudienceEditor } from '../components/AudienceEditor'
import { QuestionEditor } from '../components/QuestionEditor'
import { SurveyStatusBadge } from '../components/SurveyStatusBadge'
import {
  useDeleteSurvey,
  usePauseSurvey,
  usePublishSurvey,
  useSurvey,
  useUpdateSurvey,
} from '../hooks'
import {
  FREQUENCIES,
  isoToLocalInput,
  localInputToIso,
  PRESENTATIONS,
  serializeFilters,
  serializeQuestion,
  toFilterDraft,
  toQuestionDraft,
  validateQuestions,
  type FilterDraft,
  type SurveyQuestionDraft,
} from '../lib'

export function Component() {
  const { surveyId } = useParams<{ surveyId: string }>()
  const canManage = useHasPerm('tours:manage')
  const navigate = useNavigate()

  const surveyQuery = useSurvey(surveyId)
  const update = useUpdateSurvey()
  const publish = usePublishSurvey()
  const pause = usePauseSurvey()
  const remove = useDeleteSurvey()

  const [name, setName] = useState('')
  const [presentation, setPresentation] = useState<SurveyPresentation>('slideout')
  const [thanksMessage, setThanksMessage] = useState('Thanks for the feedback!')
  const [accent, setAccent] = useState('#6366f1')
  const [triggerType, setTriggerType] = useState<'manual' | 'url_match'>('url_match')
  const [urlPattern, setUrlPattern] = useState('*')
  const [audienceType, setAudienceType] = useState<'all' | 'filters'>('all')
  const [filters, setFilters] = useState<FilterDraft[]>([])
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [frequencyType, setFrequencyType] = useState<FrequencyType>('once')
  const [cooldownHours, setCooldownHours] = useState(24)
  const [priority, setPriority] = useState(0)
  const [questions, setQuestions] = useState<SurveyQuestionDraft[]>([])
  const [confirmDelete, setConfirmDelete] = useState(false)

  const survey = surveyQuery.data
  useEffect(() => {
    if (!survey) return
    setName(survey.name)
    setPresentation((survey.presentation as SurveyPresentation) ?? 'slideout')
    setThanksMessage(survey.thanks_message ?? 'Thanks for the feedback!')
    setAccent(survey.theme?.accent ?? '#6366f1')
    setTriggerType(survey.trigger?.type ?? 'url_match')
    setUrlPattern(survey.trigger?.url_pattern ?? '*')
    setAudienceType(survey.audience?.type ?? 'all')
    setFilters((survey.audience?.filters ?? []).map(toFilterDraft))
    setStartAt(isoToLocalInput(survey.schedule?.start_at))
    setEndAt(isoToLocalInput(survey.schedule?.end_at))
    setFrequencyType(survey.frequency?.type ?? 'once')
    setCooldownHours(survey.frequency?.cooldown_hours ?? 24)
    setPriority(survey.priority ?? 0)
    setQuestions((survey.questions ?? []).map(toQuestionDraft))
  }, [survey])

  async function save() {
    if (!surveyId) return
    const problem = validateQuestions(questions)
    if (problem) {
      toast.error(problem)
      return
    }
    await update.mutateAsync({
      id: surveyId,
      body: {
        name: name.trim(),
        questions: questions.map(serializeQuestion),
        presentation,
        thanks_message: thanksMessage,
        theme: { accent },
        trigger: {
          type: triggerType,
          url_pattern: triggerType === 'url_match' ? urlPattern.trim() || '*' : null,
        },
        audience: {
          type: audienceType,
          filters: audienceType === 'filters' ? serializeFilters(filters) : [],
        },
        schedule: { start_at: localInputToIso(startAt), end_at: localInputToIso(endAt) },
        frequency: {
          type: frequencyType,
          ...(frequencyType === 'every_time' ? { cooldown_hours: cooldownHours } : {}),
        },
        priority,
      },
    })
  }

  if (surveyQuery.isLoading) {
    return (
      <div className="flex h-full flex-col overflow-hidden">
        <div className="border-b px-6 py-4">
          <Skeleton className="h-8 w-64" />
        </div>
        <div className="flex-1 space-y-3 p-6">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    )
  }

  if (surveyQuery.isError || !survey) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card className="p-6 text-center text-sm text-muted-foreground">
          Could not load this survey.{' '}
          <Button variant="link" asChild className="px-1">
            <Link to="/surveys">Back to surveys</Link>
          </Button>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b px-6 py-4">
        <Button variant="ghost" size="icon" asChild aria-label="Back to surveys">
          <Link to="/surveys">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-lg font-semibold">{name || 'Untitled survey'}</h1>
            <SurveyStatusBadge status={survey.status} />
            <span className="text-xs text-muted-foreground">v{survey.version}</span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link to={`/surveys/${survey.id}/results`}>
              <BarChart3 className="size-4" /> Results
            </Link>
          </Button>
          {canManage ? (
            <>
              {survey.status === 'live' ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={pause.isPending}
                  onClick={() => pause.mutate(survey.id)}
                >
                  <PauseCircle className="size-4" /> Pause
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={publish.isPending}
                  onClick={() => publish.mutate(survey.id)}
                >
                  <PlayCircle className="size-4" /> Publish
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon"
                aria-label="Delete survey"
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 className="size-4" />
              </Button>
              <Button size="sm" onClick={save} disabled={update.isPending}>
                <Save className="size-4" /> {update.isPending ? 'Saving…' : 'Save'}
              </Button>
            </>
          ) : null}
        </div>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[320px_1fr]">
          <div className="grid gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Settings</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="survey-name">Name</Label>
                  <Input
                    id="survey-name"
                    value={name}
                    disabled={!canManage}
                    onChange={(e) => setName(e.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="survey-presentation">Presentation</Label>
                  <NativeSelect
                    id="survey-presentation"
                    className="w-full"
                    value={presentation}
                    disabled={!canManage}
                    onChange={(e) => setPresentation(e.target.value as SurveyPresentation)}
                  >
                    {PRESENTATIONS.map((option) => (
                      <NativeSelectOption key={option.value} value={option.value}>
                        {option.label}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="survey-thanks">Thank-you message (markdown)</Label>
                  <Textarea
                    id="survey-thanks"
                    rows={2}
                    value={thanksMessage}
                    disabled={!canManage}
                    onChange={(e) => setThanksMessage(e.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="survey-accent">Accent color</Label>
                  <div className="flex items-center gap-2">
                    <input
                      id="survey-accent"
                      type="color"
                      className="h-9 w-12 cursor-pointer rounded-md border bg-transparent"
                      value={accent}
                      disabled={!canManage}
                      onChange={(e) => setAccent(e.target.value)}
                    />
                    <Input
                      className="font-mono text-xs"
                      aria-label="Accent hex"
                      value={accent}
                      disabled={!canManage}
                      onChange={(e) => setAccent(e.target.value)}
                    />
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Targeting</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="survey-trigger">Trigger</Label>
                  <NativeSelect
                    id="survey-trigger"
                    className="w-full"
                    value={triggerType}
                    disabled={!canManage}
                    onChange={(e) => setTriggerType(e.target.value as 'manual' | 'url_match')}
                  >
                    <NativeSelectOption value="manual">Manual / API</NativeSelectOption>
                    <NativeSelectOption value="url_match">On URL match</NativeSelectOption>
                  </NativeSelect>
                </div>
                {triggerType === 'url_match' ? (
                  <div className="grid gap-1.5">
                    <Label htmlFor="survey-url">URL pattern</Label>
                    <Input
                      id="survey-url"
                      className="font-mono text-xs"
                      placeholder="*"
                      value={urlPattern}
                      disabled={!canManage}
                      onChange={(e) => setUrlPattern(e.target.value)}
                    />
                  </div>
                ) : null}

                <AudienceEditor
                  type={audienceType}
                  filters={filters}
                  disabled={!canManage}
                  onTypeChange={setAudienceType}
                  onFiltersChange={setFilters}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Schedule &amp; frequency</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-3">
                <div className="grid gap-1.5">
                  <Label htmlFor="survey-start">Starts</Label>
                  <Input
                    id="survey-start"
                    type="datetime-local"
                    value={startAt}
                    disabled={!canManage}
                    onChange={(e) => setStartAt(e.target.value)}
                  />
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="survey-end">Ends</Label>
                  <Input
                    id="survey-end"
                    type="datetime-local"
                    value={endAt}
                    disabled={!canManage}
                    onChange={(e) => setEndAt(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    Leave both empty to run continuously.
                  </p>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor="survey-frequency">Frequency</Label>
                  <NativeSelect
                    id="survey-frequency"
                    className="w-full"
                    value={frequencyType}
                    disabled={!canManage}
                    onChange={(e) => setFrequencyType(e.target.value as FrequencyType)}
                  >
                    {FREQUENCIES.map((option) => (
                      <NativeSelectOption key={option.value} value={option.value}>
                        {option.label}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                </div>
                {frequencyType === 'every_time' ? (
                  <div className="grid gap-1.5">
                    <Label htmlFor="survey-cooldown">Cooldown (hours)</Label>
                    <Input
                      id="survey-cooldown"
                      type="number"
                      min={0}
                      max={8760}
                      value={cooldownHours}
                      disabled={!canManage}
                      onChange={(e) => setCooldownHours(Number(e.target.value))}
                    />
                  </div>
                ) : null}
                <div className="grid gap-1.5">
                  <Label htmlFor="survey-priority">Priority</Label>
                  <Input
                    id="survey-priority"
                    type="number"
                    min={-100}
                    max={100}
                    value={priority}
                    disabled={!canManage}
                    onChange={(e) => setPriority(Number(e.target.value))}
                  />
                  <p className="text-xs text-muted-foreground">
                    Higher wins when several experiences match the same page.
                  </p>
                </div>
              </CardContent>
            </Card>
          </div>

          <div>
            <h2 className="mb-3 text-sm font-medium">Questions</h2>
            <QuestionEditor
              questions={questions}
              disabled={!canManage}
              onChange={setQuestions}
            />
          </div>
        </div>
      </div>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{survey.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently removes the survey and every response collected so far. This cannot
              be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                await remove.mutateAsync(survey.id)
                setConfirmDelete(false)
                navigate('/surveys')
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

export default Component
