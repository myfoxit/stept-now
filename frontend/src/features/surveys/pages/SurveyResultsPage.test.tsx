import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import {
  makeQuestion,
  makeResponsePage,
  makeResults,
  makeSurvey,
  resetAuth,
  seedAuth,
} from '../test-utils'
import { Component as SurveyResultsPage } from './SurveyResultsPage'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function renderResults() {
  return renderApp(
    <Routes>
      <Route path="/surveys/:surveyId/results" element={<SurveyResultsPage />} />
    </Routes>,
    { route: '/surveys/sv1/results' }
  )
}

const surveyWithText = makeSurvey({
  id: 'sv1',
  status: 'live',
  questions: [
    makeQuestion({ id: 'q1', type: 'nps' }),
    makeQuestion({ id: 'q2', type: 'text', question: 'What should we improve?' }),
  ],
})

beforeEach(() => seedAuth())
afterEach(() => {
  cleanup()
  resetAuth()
})

describe('SurveyResultsPage', () => {
  it('renders KPI tiles and the NPS card math from a mocked results payload', async () => {
    mockFetch({
      'GET /api/v1/w/w1/surveys/sv1': () => ({ body: surveyWithText }),
      'GET /api/v1/w/w1/surveys/sv1/results': () => ({ body: makeResults() }),
      'GET /api/v1/w/w1/surveys/sv1/responses': () => ({ body: makeResponsePage() }),
    })
    renderResults()

    expect(await screen.findByText('Responses')).toBeInTheDocument()
    expect(screen.getByText('20')).toBeInTheDocument()
    expect(screen.getByText('75%')).toBeInTheDocument()
    expect(screen.getByText('15 of 20 finished')).toBeInTheDocument()

    // NPS: score hero + 10/5/5 split as 50% / 25% / 25%.
    expect(screen.getByText('40')).toBeInTheDocument()
    expect(screen.getByText(/20 scored responses/)).toBeInTheDocument()
    const nps = within(screen.getByText('Net Promoter Score').closest('[data-slot="card"]')!)
    expect(nps.getByText('Promoters')).toBeInTheDocument()
    expect(nps.getByText('50%')).toBeInTheDocument()
    expect(nps.getAllByText('25%')).toHaveLength(2)
    expect(nps.getByText('10')).toBeInTheDocument()
  })

  it('renders the rating distribution and select breakdown tables', async () => {
    mockFetch({
      'GET /api/v1/w/w1/surveys/sv1': () => ({ body: surveyWithText }),
      'GET /api/v1/w/w1/surveys/sv1/results': () => ({ body: makeResults() }),
      'GET /api/v1/w/w1/surveys/sv1/responses': () => ({ body: makeResponsePage() }),
    })
    renderResults()

    expect(await screen.findByText('Rating distribution')).toBeInTheDocument()
    expect(screen.getByText('Average 4.2 out of 5')).toBeInTheDocument()
    const ratings = within(screen.getByText('Rating distribution').closest('[data-slot="card"]')!)
    expect(ratings.getByRole('row', { name: /1 star/ })).toBeInTheDocument()
    expect(ratings.getByRole('row', { name: /5 stars/ })).toBeInTheDocument()
    // 12 of 20 rated answers = 60%.
    expect(ratings.getByText('60%')).toBeInTheDocument()

    const select = within(
      screen.getByText('What do you use Stept for?').closest('[data-slot="card"]')!
    )
    // Sorted by count desc: Support (9), Onboarding (6), Both (2).
    const labels = select.getAllByRole('rowheader').map((cell) => cell.textContent)
    expect(labels).toEqual(['Support', 'Onboarding', 'Both'])
  })

  it('lists paged text answers with contact and anonymous attribution', async () => {
    mockFetch({
      'GET /api/v1/w/w1/surveys/sv1': () => ({ body: surveyWithText }),
      'GET /api/v1/w/w1/surveys/sv1/results': () => ({ body: makeResults() }),
      'GET /api/v1/w/w1/surveys/sv1/responses': () => ({ body: makeResponsePage() }),
    })
    renderResults()

    expect(
      await screen.findByText('The inbox is fast and the AI drafts are useful.')
    ).toBeInTheDocument()
    expect(screen.getByText('More keyboard shortcuts please.')).toBeInTheDocument()
    expect(screen.getAllByTestId('text-answer')).toHaveLength(2)
    expect(screen.getByText(/Anonymous/)).toBeInTheDocument()
    // Only the text question's answers appear — the NPS score (9) is not listed.
    expect(screen.queryByText(/What should we improve\? · c1/)).toBeInTheDocument()
  })

  it('pages through responses with Previous/Next', async () => {
    const seen: string[] = []
    const fetchMock = mockFetch({
      'GET /api/v1/w/w1/surveys/sv1': () => ({ body: surveyWithText }),
      'GET /api/v1/w/w1/surveys/sv1/results': () => ({ body: makeResults() }),
      'GET /api/v1/w/w1/surveys/sv1/responses': () => ({ body: makeResponsePage({ total: 24 }) }),
    })
    renderResults()

    expect(await screen.findByText(/Responses 1–2 of 24/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => {
      for (const call of fetchMock.mock.calls) seen.push(String(call[0]))
      expect(seen.some((url) => url.includes('offset=10'))).toBe(true)
    })
  })

  it('shows an error state with retry when results fail', async () => {
    let failed = false
    mockFetch({
      'GET /api/v1/w/w1/surveys/sv1': () => ({ body: surveyWithText }),
      'GET /api/v1/w/w1/surveys/sv1/responses': () => ({ body: makeResponsePage() }),
      'GET /api/v1/w/w1/surveys/sv1/results': () => {
        if (!failed) {
          failed = true
          return { status: 500, body: { error: { code: 'boom', message: 'boom' } } }
        }
        return { body: makeResults() }
      },
    })
    renderResults()

    expect(await screen.findByText(/Could not load results/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Net Promoter Score')).toBeInTheDocument()
  })

  it('omits the NPS and rating cards when the payload has none', async () => {
    mockFetch({
      'GET /api/v1/w/w1/surveys/sv1': () => ({ body: surveyWithText }),
      'GET /api/v1/w/w1/surveys/sv1/results': () => ({
        body: makeResults({ nps: null, ratings: null, select: [], by_day: [] }),
      }),
      'GET /api/v1/w/w1/surveys/sv1/responses': () => ({ body: makeResponsePage() }),
    })
    renderResults()

    expect(await screen.findByText('Responses over time')).toBeInTheDocument()
    expect(screen.queryByText('Net Promoter Score')).not.toBeInTheDocument()
    expect(screen.queryByText('Rating distribution')).not.toBeInTheDocument()
    expect(screen.getByText('No responses yet.')).toBeInTheDocument()
  })

  it('blocks the page without tours:read', async () => {
    seedAuth([])
    mockFetch({})
    renderResults()

    expect(await screen.findByText(/don’t have access to surveys/)).toBeInTheDocument()
  })
})
