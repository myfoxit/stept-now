import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { makeStep, makeTour, resetAuth, seedAuth } from '../test-utils'
import { Component as TourEditorPage } from './TourEditorPage'

const toastError = vi.hoisted(() => vi.fn())
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: toastError, message: vi.fn() },
}))

function renderEditor() {
  return renderApp(
    <Routes>
      <Route path="/tours/:tourId" element={<TourEditorPage />} />
    </Routes>,
    { route: '/tours/t1' }
  )
}

/** GET the tour, capture whatever the Save button PATCHes. */
function mockTour(tour = makeTour()) {
  const captured: { body?: Record<string, unknown> } = {}
  mockFetch({
    'GET /api/v1/w/w1/tours/t1': () => ({ body: tour }),
    'PATCH /api/v1/w/w1/tours/t1': (init) => {
      captured.body = JSON.parse(init!.body as string)
      return { body: tour }
    },
    'POST /api/v1/w/w1/tours/t1/preview-token': () => ({
      body: { token: 'prev-tok-123', expires_minutes: 60 },
    }),
  })
  return captured
}

beforeEach(() => {
  toastError.mockClear()
  seedAuth()
})
afterEach(() => {
  cleanup()
  resetAuth()
})

describe('TourEditorPage', () => {
  it('sends the audience filters on save (regression: audience was never sent)', async () => {
    const captured = mockTour()
    renderEditor()

    await screen.findByTestId('tour-step')
    await userEvent.selectOptions(screen.getByLabelText('Who sees it'), 'filters')
    await userEvent.click(screen.getByRole('button', { name: /Add filter/ }))
    await userEvent.selectOptions(screen.getByLabelText('Filter 1 field'), 'attributes')
    await userEvent.type(screen.getByLabelText('Filter 1 attribute key'), 'plan')
    await userEvent.selectOptions(screen.getByLabelText('Filter 1 operator'), 'contains')
    await userEvent.type(screen.getByLabelText('Filter 1 value'), 'pro')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))

    await waitFor(() => expect(captured.body).toBeDefined())
    expect(captured.body!.audience).toEqual({
      type: 'filters',
      filters: [{ field: 'attributes.plan', op: 'contains', value: 'pro' }],
    })
  })

  it('serializes schedule, frequency, priority, mode and the behaviour switches', async () => {
    const captured = mockTour()
    renderEditor()

    await screen.findByTestId('tour-step')
    await userEvent.type(screen.getByLabelText('Starts'), '2026-08-01T09:30')
    await userEvent.selectOptions(screen.getByLabelText('Show it'), 'every_time')
    await userEvent.type(screen.getByLabelText('Cooldown (hours)'), '12')
    await userEvent.clear(screen.getByLabelText('Priority'))
    await userEvent.type(screen.getByLabelText('Priority'), '7')
    await userEvent.selectOptions(screen.getByLabelText('Mode'), 'driven')
    await userEvent.click(screen.getByLabelText('Backdrop'))
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))

    await waitFor(() => expect(captured.body).toBeDefined())
    expect(captured.body!.frequency).toEqual({ type: 'every_time', cooldown_hours: 12 })
    expect(captured.body!.priority).toBe(7)
    expect(captured.body!.settings).toEqual({
      mode: 'driven',
      backdrop: false,
      show_progress: true,
      dismissable: true,
    })
    const schedule = captured.body!.schedule as { start_at: string; end_at: null }
    expect(new Date(schedule.start_at).getTime()).toBe(new Date('2026-08-01T09:30').getTime())
    expect(schedule.end_at).toBeNull()
  })

  it('keeps the recorder target and screenshot key across an edit and save', async () => {
    const target = { selectors: [{ kind: 'css', value: '#signup', score: 0.9 }], text: 'Sign up' }
    const captured = mockTour(
      makeTour({
        steps: [
          makeStep({
            id: 's1',
            target,
            screenshot_key: 'public/w1/2026/07/shot.png',
            fallback_selectors: ['.signup'],
          }),
        ],
      })
    )
    renderEditor()

    const card = within(await screen.findByTestId('tour-step'))
    expect(screen.getByTestId('recorder-target')).toBeInTheDocument()
    await userEvent.type(card.getByLabelText('Title'), ' now')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))

    await waitFor(() => expect(captured.body).toBeDefined())
    expect(captured.body!.steps).toEqual([
      expect.objectContaining({
        id: 's1',
        target,
        screenshot_key: 'public/w1/2026/07/shot.png',
        fallback_selectors: ['.signup'],
        title: 'Start here now',
      }),
    ])
  })

  it('serializes a step-type change into the tagged-union payload', async () => {
    const captured = mockTour()
    renderEditor()

    const card = within(await screen.findByTestId('tour-step'))
    await userEvent.selectOptions(card.getByLabelText('Type'), 'action')
    await userEvent.selectOptions(card.getByLabelText('Action'), 'fill')
    await userEvent.type(card.getByLabelText('Value to type'), 'ada@acme.test')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))

    await waitFor(() => expect(captured.body).toBeDefined())
    expect(captured.body!.steps).toEqual([
      expect.objectContaining({
        type: 'action',
        action: { kind: 'fill', value: 'ada@acme.test' },
      }),
    ])
    expect((captured.body!.steps as Record<string, unknown>[])[0]!.wait).toBeUndefined()
  })

  it('blocks the save and explains when a step is missing a selector', async () => {
    const captured = mockTour(makeTour({ steps: [makeStep({ id: 's1', selector: '' })] }))
    renderEditor()

    await screen.findByTestId('tour-step')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/Step 1 needs a CSS selector/))
    )
    expect(captured.body).toBeUndefined()
  })

  it('mints a preview token and copies the hash link', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    mockTour()
    renderEditor()

    await screen.findByTestId('tour-step')
    await userEvent.click(screen.getByRole('button', { name: /copy preview link/i }))

    await waitFor(() => expect(screen.getByTestId('preview-link')).toBeInTheDocument())
    expect(screen.getByTestId('preview-link')).toHaveTextContent('#stept-preview=prev-tok-123')
    expect(writeText).toHaveBeenCalledWith(
      'https://your-site.example.com/#stept-preview=prev-tok-123'
    )
  })

  it('previews the selected step and shows banner position only for banner tours', async () => {
    mockTour(makeTour({ kind: 'banner', theme: { accent: '#6366f1', position: 'top' } }))
    renderEditor()

    await screen.findByTestId('tour-step')
    expect(screen.getByLabelText('Banner position')).toBeInTheDocument()
    // Preview renders the step's markdown body as formatted text.
    const preview = within(screen.getByTestId('step-preview'))
    expect(preview.getByText('Start here')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Kind'), 'flow')
    expect(screen.queryByLabelText('Banner position')).not.toBeInTheDocument()
  })

  it('hides mutating controls and disables inputs without tours:manage', async () => {
    seedAuth(['tours:read'])
    mockTour()
    renderEditor()

    await screen.findByTestId('tour-step')
    expect(screen.queryByRole('button', { name: /^Save/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Publish/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Delete tour/ })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Name')).toBeDisabled()
    expect(screen.getByRole('button', { name: /copy preview link/i })).toBeDisabled()
  })

  it('shows an error card when the tour cannot be loaded', async () => {
    mockFetch({
      'GET /api/v1/w/w1/tours/t1': () => ({
        status: 404,
        body: { error: { code: 'not_found', message: 'Tour not found' } },
      }),
    })
    renderEditor()

    expect(await screen.findByText(/Could not load this tour/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to tours' })).toBeInTheDocument()
  })
})
