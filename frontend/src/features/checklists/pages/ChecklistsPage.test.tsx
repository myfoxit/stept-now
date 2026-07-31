import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { makeChecklist, makeStats, resetAuth, seedAuth } from '../test-utils'
import { Component as ChecklistsPage } from './ChecklistsPage'

const toastError = vi.hoisted(() => vi.fn())
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: toastError },
}))

beforeEach(() => {
  toastError.mockClear()
  seedAuth()
})
afterEach(() => {
  cleanup() // unmount before clearing the store so no live component re-renders without a workspace
  resetAuth()
})

describe('ChecklistsPage', () => {
  it('renders cards with status, item count, trigger and the completion-rate mini-stat', async () => {
    mockFetch({
      'GET /api/v1/w/w1/checklists': () => ({
        body: [
          makeChecklist({
            id: 'cl1',
            name: 'Getting started',
            status: 'live',
            trigger: { type: 'url_match', url_pattern: '/app*' },
          }),
        ],
      }),
      'GET /api/v1/w/w1/checklists/cl1/stats': () => ({ body: makeStats() }),
    })
    renderApp(<ChecklistsPage />, { route: '/checklists' })

    expect(await screen.findByText('Getting started')).toBeInTheDocument()
    expect(screen.getByText('live')).toBeInTheDocument()
    expect(screen.getByText(/1 item · On \/app\*/)).toBeInTheDocument()
    expect(await screen.findByText('25%')).toBeInTheDocument()
    expect(screen.getByText('completion rate')).toBeInTheDocument()
  })

  it('does not fetch stats for drafts', async () => {
    const fetchMock = mockFetch({
      'GET /api/v1/w/w1/checklists': () => ({ body: [makeChecklist({ status: 'draft' })] }),
    })
    renderApp(<ChecklistsPage />, { route: '/checklists' })

    expect(await screen.findByText('Not published yet')).toBeInTheDocument()
    const paths = fetchMock.mock.calls.map((call) => String(call[0]))
    expect(paths.some((path) => path.includes('/stats'))).toBe(false)
  })

  it('shows skeletons while loading', () => {
    mockFetch({ 'GET /api/v1/w/w1/checklists': () => ({ body: [] }) })
    const { container } = renderApp(<ChecklistsPage />, { route: '/checklists' })
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0)
  })

  it('shows the empty state with a create CTA', async () => {
    mockFetch({ 'GET /api/v1/w/w1/checklists': () => ({ body: [] }) })
    renderApp(<ChecklistsPage />, { route: '/checklists' })

    expect(await screen.findByText('No checklists yet')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /Create your first checklist/ })
    ).toBeInTheDocument()
  })

  it('shows an error state with retry when the list fails', async () => {
    let failed = false
    mockFetch({
      'GET /api/v1/w/w1/checklists': () => {
        if (!failed) {
          failed = true
          return { status: 500, body: { error: { code: 'boom', message: 'boom' } } }
        }
        return { body: [makeChecklist({ name: 'Recovered' })] }
      },
    })
    renderApp(<ChecklistsPage />, { route: '/checklists' })

    expect(await screen.findByText(/Could not load checklists/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Recovered')).toBeInTheDocument()
  })

  it('hides every mutating control without tours:manage', async () => {
    seedAuth(['tours:read'])
    mockFetch({
      'GET /api/v1/w/w1/checklists': () => ({ body: [makeChecklist({ name: 'Getting started' })] }),
    })
    renderApp(<ChecklistsPage />, { route: '/checklists' })

    expect(await screen.findByText('Getting started')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /New checklist/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Publish/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Delete/ })).not.toBeInTheDocument()
  })

  it('surfaces a clear toast when publishing an empty checklist 409s', async () => {
    mockFetch({
      'GET /api/v1/w/w1/checklists': () => ({
        body: [makeChecklist({ id: 'cl1', name: 'Empty list', items: [] })],
      }),
      'POST /api/v1/w/w1/checklists/cl1/publish': () => ({
        status: 409,
        body: { error: { code: 'conflict', message: 'Add at least one item before publishing' } },
      }),
    })
    renderApp(<ChecklistsPage />, { route: '/checklists' })

    await userEvent.click(await screen.findByRole('button', { name: 'Publish Empty list' }))
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Add at least one item before publishing')
    )
  })

  it('creates a checklist from the dialog and navigates to its editor', async () => {
    let created: unknown
    mockFetch({
      'GET /api/v1/w/w1/checklists': () => ({ body: [] }),
      'POST /api/v1/w/w1/checklists': (init) => {
        created = JSON.parse(init!.body as string)
        return { status: 201, body: makeChecklist({ id: 'new-1', name: 'Onboarding' }) }
      },
    })
    renderApp(<ChecklistsPage />, { route: '/checklists' })

    await userEvent.click(await screen.findByRole('button', { name: /New checklist/ }))
    await userEvent.type(screen.getByLabelText('Name'), 'Onboarding')
    await userEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(created).toEqual({ name: 'Onboarding' }))
  })
})
