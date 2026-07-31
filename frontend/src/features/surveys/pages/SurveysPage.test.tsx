import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { makeResults, makeSurvey, resetAuth, seedAuth } from '../test-utils'
import { Component as SurveysPage } from './SurveysPage'

const toastError = vi.hoisted(() => vi.fn())
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: toastError },
}))

beforeEach(() => {
  toastError.mockClear()
  seedAuth()
})
afterEach(() => {
  cleanup()
  resetAuth()
})

describe('SurveysPage', () => {
  it('renders cards with status, question count, presentation and response counts', async () => {
    mockFetch({
      'GET /api/v1/w/w1/surveys': () => ({
        body: [makeSurvey({ id: 'sv1', status: 'live' })],
      }),
      'GET /api/v1/w/w1/surveys/sv1/results': () => ({ body: makeResults() }),
    })
    renderApp(<SurveysPage />, { route: '/surveys' })

    expect(await screen.findByText('How are we doing?')).toBeInTheDocument()
    expect(screen.getByText('live')).toBeInTheDocument()
    expect(screen.getByText(/1 question · slideout · On \*/)).toBeInTheDocument()
    expect(await screen.findByText('20')).toBeInTheDocument()
    expect(screen.getByText('75%')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Results for How are we doing?' })).toHaveAttribute(
      'href',
      '/surveys/sv1/results'
    )
  })

  it('shows skeletons while loading and the empty state afterwards', async () => {
    mockFetch({ 'GET /api/v1/w/w1/surveys': () => ({ body: [] }) })
    const { container } = renderApp(<SurveysPage />, { route: '/surveys' })
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0)

    expect(await screen.findByText('No surveys yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Create your first survey/ })).toBeInTheDocument()
  })

  it('shows an error state with retry when the list fails', async () => {
    let failed = false
    mockFetch({
      'GET /api/v1/w/w1/surveys': () => {
        if (!failed) {
          failed = true
          return { status: 500, body: { error: { code: 'boom', message: 'boom' } } }
        }
        return { body: [makeSurvey({ name: 'Recovered' })] }
      },
    })
    renderApp(<SurveysPage />, { route: '/surveys' })

    expect(await screen.findByText(/Could not load surveys/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Recovered')).toBeInTheDocument()
  })

  it('hides every mutating control without tours:manage but keeps the results link', async () => {
    seedAuth(['tours:read'])
    mockFetch({ 'GET /api/v1/w/w1/surveys': () => ({ body: [makeSurvey()] }) })
    renderApp(<SurveysPage />, { route: '/surveys' })

    expect(await screen.findByText('How are we doing?')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /New survey/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Publish/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Delete/ })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^Results for/ })).toBeInTheDocument()
  })

  it('surfaces a clear toast when publishing a question-less survey 409s', async () => {
    mockFetch({
      'GET /api/v1/w/w1/surveys': () => ({
        body: [makeSurvey({ id: 'sv1', name: 'Empty survey', questions: [] })],
      }),
      'POST /api/v1/w/w1/surveys/sv1/publish': () => ({
        status: 409,
        body: {
          error: { code: 'conflict', message: 'Add at least one question before publishing' },
        },
      }),
    })
    renderApp(<SurveysPage />, { route: '/surveys' })

    await userEvent.click(await screen.findByRole('button', { name: 'Publish Empty survey' }))
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Add at least one question before publishing')
    )
  })

  it('creates a survey from the dialog', async () => {
    let created: unknown
    mockFetch({
      'GET /api/v1/w/w1/surveys': () => ({ body: [] }),
      'POST /api/v1/w/w1/surveys': (init) => {
        created = JSON.parse(init!.body as string)
        return { status: 201, body: makeSurvey({ id: 'new-1', name: 'Post-onboarding NPS' }) }
      },
    })
    renderApp(<SurveysPage />, { route: '/surveys' })

    await userEvent.click(await screen.findByRole('button', { name: /New survey/ }))
    await userEvent.type(screen.getByLabelText('Name'), 'Post-onboarding NPS')
    await userEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(created).toEqual({ name: 'Post-onboarding NPS' }))
  })
})
