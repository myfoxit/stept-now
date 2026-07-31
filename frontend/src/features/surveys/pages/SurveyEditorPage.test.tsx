import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { makeQuestion, makeSurvey, resetAuth, seedAuth } from '../test-utils'
import { Component as SurveyEditorPage } from './SurveyEditorPage'

const toastError = vi.hoisted(() => vi.fn())
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: toastError },
}))

function renderEditor() {
  return renderApp(
    <Routes>
      <Route path="/surveys/:surveyId" element={<SurveyEditorPage />} />
    </Routes>,
    { route: '/surveys/sv1' }
  )
}

beforeEach(() => {
  toastError.mockClear()
  seedAuth()
})
afterEach(() => {
  cleanup()
  resetAuth()
})

describe('SurveyEditorPage', () => {
  it('serializes the question builder into the PATCH payload, options for select only', async () => {
    let patched: Record<string, unknown> | undefined
    mockFetch({
      'GET /api/v1/w/w1/surveys/sv1': () => ({ body: makeSurvey({ id: 'sv1' }) }),
      'PATCH /api/v1/w/w1/surveys/sv1': (init) => {
        patched = JSON.parse(init!.body as string)
        return { body: makeSurvey({ id: 'sv1' }) }
      },
    })
    renderEditor()

    const first = within(await screen.findByTestId('survey-question'))
    await userEvent.selectOptions(first.getByLabelText('Type'), 'select')
    await userEvent.type(first.getByLabelText('Question 1 option 1'), 'Support')
    await userEvent.type(first.getByLabelText('Question 1 option 2'), 'Onboarding')
    await userEvent.click(first.getByRole('switch', { name: 'Question 1 required' }))

    await userEvent.click(screen.getByRole('button', { name: /Add question/ }))
    const second = within(screen.getAllByTestId('survey-question')[1]!)
    await userEvent.type(second.getByLabelText('Question'), 'Anything else?')

    await userEvent.click(screen.getByRole('button', { name: /Save/ }))

    await waitFor(() => expect(patched).toBeDefined())
    expect(patched!.questions).toEqual([
      {
        id: 'q1',
        type: 'select',
        question: 'How likely are you to recommend Stept?',
        required: false,
        options: ['Support', 'Onboarding'],
      },
      { type: 'text', question: 'Anything else?', required: true },
    ])
  })

  it('serializes presentation, schedule, frequency cooldown and priority', async () => {
    let patched: Record<string, unknown> | undefined
    mockFetch({
      'GET /api/v1/w/w1/surveys/sv1': () => ({ body: makeSurvey({ id: 'sv1' }) }),
      'PATCH /api/v1/w/w1/surveys/sv1': (init) => {
        patched = JSON.parse(init!.body as string)
        return { body: makeSurvey({ id: 'sv1' }) }
      },
    })
    renderEditor()

    await screen.findByTestId('survey-question')
    await userEvent.selectOptions(screen.getByLabelText('Presentation'), 'modal')
    await userEvent.type(screen.getByLabelText('Starts'), '2026-08-01T09:30')

    // The cooldown input only exists for `every_time`.
    expect(screen.queryByLabelText('Cooldown (hours)')).not.toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Frequency'), 'every_time')
    const cooldown = screen.getByLabelText('Cooldown (hours)')
    await userEvent.clear(cooldown)
    await userEvent.type(cooldown, '72')

    await userEvent.clear(screen.getByLabelText('Priority'))
    await userEvent.type(screen.getByLabelText('Priority'), '3')
    await userEvent.click(screen.getByRole('button', { name: /Save/ }))

    await waitFor(() => expect(patched).toBeDefined())
    expect(patched!.presentation).toBe('modal')
    expect(patched!.frequency).toEqual({ type: 'every_time', cooldown_hours: 72 })
    expect(patched!.priority).toBe(3)
    expect((patched!.schedule as Record<string, unknown>).end_at).toBeNull()
    expect((patched!.schedule as Record<string, unknown>).start_at).toMatch(
      /^2026-08-01T\d{2}:\d{2}:00\.000Z$/
    )
  })

  it('blocks saving and toasts when a select question has too few options', async () => {
    const fetchMock = mockFetch({
      'GET /api/v1/w/w1/surveys/sv1': () => ({
        body: makeSurvey({
          id: 'sv1',
          questions: [makeQuestion({ id: 'q1', type: 'select', options: ['Yes', 'No'] })],
        }),
      }),
    })
    renderEditor()

    const first = within(await screen.findByTestId('survey-question'))
    await userEvent.clear(first.getByLabelText('Question 1 option 2'))
    await userEvent.click(screen.getByRole('button', { name: /Save/ }))

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/between 2 and 6 options/))
    )
    expect(
      fetchMock.mock.calls.some((call) => (call[1] as RequestInit | undefined)?.method === 'PATCH')
    ).toBe(false)
  })

  it('hides mutating controls and disables inputs without tours:manage', async () => {
    seedAuth(['tours:read'])
    mockFetch({ 'GET /api/v1/w/w1/surveys/sv1': () => ({ body: makeSurvey({ id: 'sv1' }) }) })
    renderEditor()

    await screen.findByTestId('survey-question')
    expect(screen.queryByRole('button', { name: /Save/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Publish/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Delete survey/ })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Presentation')).toBeDisabled()
    // Reading results stays available to tours:read.
    expect(screen.getByRole('link', { name: /Results/ })).toBeInTheDocument()
  })

  it('shows an error card when the survey cannot be loaded', async () => {
    mockFetch({
      'GET /api/v1/w/w1/surveys/sv1': () => ({
        status: 404,
        body: { error: { code: 'not_found', message: 'Survey not found' } },
      }),
    })
    renderEditor()

    expect(await screen.findByText(/Could not load this survey/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to surveys' })).toBeInTheDocument()
  })
})
