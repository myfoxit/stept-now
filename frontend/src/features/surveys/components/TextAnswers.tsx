import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { fullDateTime } from '@/lib/format'

import type { SurveyQuestion } from '../api'
import { useSurveyResponses } from '../hooks'

const PAGE_SIZE = 10

/** Written feedback, paged straight off `GET /surveys/{id}/responses`. */
export function TextAnswers({
  surveyId,
  questions,
}: {
  surveyId: string
  questions: SurveyQuestion[]
}) {
  const [offset, setOffset] = useState(0)
  const page = useSurveyResponses(surveyId, { limit: PAGE_SIZE, offset })

  const labels = new Map(
    questions.filter((q) => q.type === 'text').map((q) => [q.id, q.question] as const)
  )

  const entries = (page.data?.items ?? []).flatMap((response) =>
    response.answers
      .filter((answer) => labels.has(answer.question_id) && String(answer.value).trim() !== '')
      .map((answer) => ({
        key: `${response.id}:${answer.question_id}`,
        question: labels.get(answer.question_id)!,
        value: String(answer.value),
        contactId: response.contact_id ?? null,
        createdAt: response.created_at,
      }))
  )

  const total = page.data?.total ?? 0
  const shown = page.data?.items.length ?? 0

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Written feedback</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        {page.isLoading ? (
          <>
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </>
        ) : page.isError ? (
          <p className="py-4 text-center text-sm text-muted-foreground">
            Could not load responses.{' '}
            <Button variant="link" className="px-1" onClick={() => page.refetch()}>
              Retry
            </Button>
          </p>
        ) : entries.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {labels.size === 0
              ? 'This survey has no open-text question.'
              : 'No written answers on this page.'}
          </p>
        ) : (
          <ul className="grid gap-3">
            {entries.map((entry) => (
              <li key={entry.key} className="rounded-md border p-3" data-testid="text-answer">
                <p className="text-sm whitespace-pre-wrap">{entry.value}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {entry.question} · {entry.contactId ? entry.contactId : 'Anonymous'} ·{' '}
                  {fullDateTime(entry.createdAt)}
                </p>
              </li>
            ))}
          </ul>
        )}

        {total > PAGE_SIZE ? (
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-muted-foreground">
              Responses {shown === 0 ? 0 : offset + 1}–{offset + shown} of {total}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0 || page.isFetching}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + PAGE_SIZE >= total || page.isFetching}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
