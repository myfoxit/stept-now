import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { makeChecklist, makeStats, resetAuth, seedAuth, tourOptions } from '../test-utils'
import { Component as ChecklistEditorPage } from './ChecklistEditorPage'

const toastError = vi.hoisted(() => vi.fn())
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: toastError },
}))

function renderEditor() {
  return renderApp(
    <Routes>
      <Route path="/checklists/:checklistId" element={<ChecklistEditorPage />} />
    </Routes>,
    { route: '/checklists/cl1' }
  )
}

const baseRoutes = {
  'GET /api/v1/w/w1/tours': () => ({ body: tourOptions }),
  'GET /api/v1/w/w1/checklists/cl1/stats': () => ({ body: makeStats() }),
}

beforeEach(() => {
  toastError.mockClear()
  seedAuth()
})
afterEach(() => {
  cleanup()
  resetAuth()
})

describe('ChecklistEditorPage', () => {
  it('serializes item action/completion pickers into the PATCH payload', async () => {
    let patched: Record<string, unknown> | undefined
    mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/checklists/cl1': () => ({
        body: makeChecklist({ id: 'cl1', status: 'live' }),
      }),
      'PATCH /api/v1/w/w1/checklists/cl1': (init) => {
        patched = JSON.parse(init!.body as string)
        return { body: makeChecklist({ id: 'cl1' }) }
      },
    })
    renderEditor()

    // Scoped to the item card — the trigger settings also have a "URL pattern" field.
    const item = within(await screen.findByTestId('checklist-item'))
    await userEvent.selectOptions(item.getByLabelText('Button action'), 'start_tour')
    await userEvent.selectOptions(item.getByLabelText('Tour to start'), 't1')
    await userEvent.selectOptions(item.getByLabelText('Completes when'), 'url_visited')
    await userEvent.type(item.getByLabelText('URL pattern'), '*/settings*')
    await userEvent.click(screen.getByRole('button', { name: /Save/ }))

    await waitFor(() => expect(patched).toBeDefined())
    expect(patched!.items).toEqual([
      {
        id: 'i1',
        title: 'Take the welcome tour',
        body: 'Two minutes, tops.',
        action: { type: 'start_tour', tour_id: 't1' },
        completion: { type: 'url_visited', url_pattern: '*/settings*' },
      },
    ])
  })

  it('serializes audience filter rows and launcher/theme/priority settings', async () => {
    let patched: Record<string, unknown> | undefined
    mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/checklists/cl1': () => ({ body: makeChecklist({ id: 'cl1' }) }),
      'PATCH /api/v1/w/w1/checklists/cl1': (init) => {
        patched = JSON.parse(init!.body as string)
        return { body: makeChecklist({ id: 'cl1' }) }
      },
    })
    renderEditor()

    await screen.findByTestId('checklist-item')
    await userEvent.selectOptions(await screen.findByLabelText('Who sees it'), 'filters')
    await userEvent.click(screen.getByRole('button', { name: /Add filter/ }))
    await userEvent.selectOptions(screen.getByLabelText('Filter 1 field'), 'attributes')
    await userEvent.type(screen.getByLabelText('Filter 1 attribute key'), 'plan')
    await userEvent.selectOptions(screen.getByLabelText('Filter 1 operator'), 'contains')
    await userEvent.type(screen.getByLabelText('Filter 1 value'), 'pro')

    await userEvent.clear(screen.getByLabelText('Launcher label'))
    await userEvent.type(screen.getByLabelText('Launcher label'), 'Set up Stept')
    await userEvent.selectOptions(screen.getByLabelText('Position'), 'bottom-left')
    await userEvent.clear(screen.getByLabelText('Priority'))
    await userEvent.type(screen.getByLabelText('Priority'), '5')

    await userEvent.click(screen.getByRole('button', { name: /Save/ }))

    await waitFor(() => expect(patched).toBeDefined())
    expect(patched!.audience).toEqual({
      type: 'filters',
      filters: [{ field: 'attributes.plan', op: 'contains', value: 'pro' }],
    })
    expect(patched!.launcher).toEqual({ label: 'Set up Stept', auto_open_once: true })
    expect(patched!.theme).toEqual({ accent: '#6366f1', position: 'bottom-left' })
    expect(patched!.priority).toBe(5)
    expect(patched!.trigger).toEqual({ type: 'url_match', url_pattern: '*' })
  })

  it('blocks saving and toasts when a conditional item field is missing', async () => {
    const fetchMock = mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/checklists/cl1': () => ({ body: makeChecklist({ id: 'cl1' }) }),
    })
    renderEditor()

    await screen.findByTestId('checklist-item')
    await userEvent.selectOptions(screen.getByLabelText('Button action'), 'start_tour')
    await userEvent.click(screen.getByRole('button', { name: /Save/ }))

    await waitFor(() => expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/Item 1/)))
    expect(
      fetchMock.mock.calls.some((call) => (call[1] as RequestInit | undefined)?.method === 'PATCH')
    ).toBe(false)
  })

  it('renders the stats strip for a published checklist', async () => {
    mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/checklists/cl1': () => ({
        body: makeChecklist({ id: 'cl1', status: 'live' }),
      }),
    })
    renderEditor()

    expect(await screen.findByText('25%')).toBeInTheDocument()
    expect(screen.getByText('completion rate')).toBeInTheDocument()
    expect(screen.getByText('7 completed')).toBeInTheDocument()
  })

  it('hides save/publish/delete and disables inputs without tours:manage', async () => {
    seedAuth(['tours:read'])
    mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/checklists/cl1': () => ({ body: makeChecklist({ id: 'cl1' }) }),
    })
    renderEditor()

    await screen.findByTestId('checklist-item')
    expect(screen.queryByRole('button', { name: /Save/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Publish/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Delete checklist/ })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Launcher label')).toBeDisabled()
  })

  it('shows an error card when the checklist cannot be loaded', async () => {
    mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/checklists/cl1': () => ({
        status: 404,
        body: { error: { code: 'not_found', message: 'Checklist not found' } },
      }),
    })
    renderEditor()

    expect(await screen.findByText(/Could not load this checklist/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to checklists' })).toBeInTheDocument()
  })
})
