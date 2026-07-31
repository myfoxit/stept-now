import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { makeEvent, makeEventsPage, makeStats, makeTour, resetAuth, seedAuth } from '../test-utils'
import { Component as TourAnalyticsPage } from './TourAnalyticsPage'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

// Capture the realtime subscription so tests can push a `tour.event` frame.
const realtime = vi.hoisted(() => ({
  handlers: new Map<string, (data: Record<string, unknown>) => void>(),
}))
vi.mock('@/api/ws', () => ({
  useRealtime: (type: string, handler: (data: Record<string, unknown>) => void) => {
    realtime.handlers.set(type, handler)
  },
}))

function renderAnalytics() {
  return renderApp(
    <Routes>
      <Route path="/tours/:tourId/analytics" element={<TourAnalyticsPage />} />
    </Routes>,
    { route: '/tours/t1/analytics' }
  )
}

function routes(overrides: Record<string, () => { status?: number; body?: unknown }> = {}) {
  return {
    'GET /api/v1/w/w1/tours/t1': () => ({ body: makeTour({ status: 'live' }) }),
    'GET /api/v1/w/w1/tours/t1/stats': () => ({ body: makeStats() }),
    'GET /api/v1/w/w1/tours/t1/events': () => ({ body: makeEventsPage() }),
    ...overrides,
  }
}

beforeEach(() => {
  realtime.handlers.clear()
  seedAuth()
})
afterEach(() => {
  cleanup()
  resetAuth()
})

describe('TourAnalyticsPage', () => {
  it('renders the KPI tiles from the stats payload', async () => {
    mockFetch(routes())
    renderAnalytics()

    expect(await screen.findByText('Starts')).toBeInTheDocument()
    expect(screen.getByText('120')).toBeInTheDocument()
    expect(screen.getByText('Unique starts')).toBeInTheDocument()
    expect(screen.getByText('96')).toBeInTheDocument()
    expect(screen.getByText('35%')).toBeInTheDocument() // completion rate
    expect(screen.getByText('42 completed')).toBeInTheDocument()
    expect(screen.getByText('Step errors')).toBeInTheDocument()
    expect(screen.getByText('18 dismissed')).toBeInTheDocument()
  })

  it('renders a per-step funnel with drop-off and healed counts', async () => {
    mockFetch(routes())
    renderAnalytics()

    const steps = await screen.findAllByTestId('funnel-step')
    expect(steps).toHaveLength(2)
    expect(within(steps[0]!).getByText('1. Start here')).toBeInTheDocument()
    expect(within(steps[0]!).getByText('120 viewed · 30 dropped')).toBeInTheDocument()
    expect(within(steps[0]!).queryByText(/healed/)).not.toBeInTheDocument()

    expect(within(steps[1]!).getByText('90 viewed · 48 dropped')).toBeInTheDocument()
    expect(within(steps[1]!).getByText('7 healed')).toBeInTheDocument()
    expect(
      within(steps[1]!).getByRole('img', {
        name: 'Step 2: 90 viewed, 48 dropped off, 7 healed',
      })
    ).toBeInTheDocument()
  })

  it('plots starts vs completions, and says so when the window is empty', async () => {
    mockFetch(routes())
    const { unmount } = renderAnalytics()
    expect(await screen.findByText('Starts vs completions')).toBeInTheDocument()
    unmount()

    mockFetch(
      routes({
        'GET /api/v1/w/w1/tours/t1/stats': () => ({
          body: makeStats({ by_day: [{ date: '2026-07-31', starts: 0, completions: 0 }] }),
        }),
      })
    )
    renderAnalytics()
    expect(await screen.findByText('No activity in the last 30 days.')).toBeInTheDocument()
  })

  it('lists recent events with their meta and pages through them', async () => {
    const fetchMock = mockFetch(
      routes({
        'GET /api/v1/w/w1/tours/t1/events': () => ({
          body: makeEventsPage({
            total: 30,
            items: [
              makeEvent({ id: 'e1', event: 'step_error', meta: { reason: 'not_found' } }),
              makeEvent({ id: 'e2', event: 'step_viewed', meta: { healed: true } }),
              makeEvent({ id: 'e3', event: 'completed', step_index: null, contact_id: null }),
            ],
          }),
        }),
      })
    )
    renderAnalytics()

    const rows = await screen.findAllByTestId('event-row')
    expect(rows).toHaveLength(3)
    expect(within(rows[0]!).getByText('Step error')).toBeInTheDocument()
    expect(within(rows[0]!).getByText('not_found')).toBeInTheDocument()
    expect(within(rows[1]!).getByText('healed')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('anonymous')).toBeInTheDocument()
    expect(screen.getByText('1–3 of 30')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('offset=25'))).toBe(true)
    )
  })

  it('live-appends a realtime tour.event for this tour only', async () => {
    mockFetch(routes())
    renderAnalytics()
    await screen.findAllByTestId('event-row')
    const handler = realtime.handlers.get('tour.event')!
    expect(handler).toBeDefined()

    act(() =>
      handler({
        id: 'live-1',
        tour_id: 'other-tour',
        event: 'completed',
        step_index: null,
        contact_id: null,
        meta: {},
        created_at: '2026-07-31T11:00:00Z',
      })
    )
    expect(screen.getAllByTestId('event-row')).toHaveLength(1)

    act(() =>
      handler({
        id: 'live-2',
        tour_id: 't1',
        event: 'completed',
        step_index: null,
        contact_id: null,
        meta: {},
        created_at: '2026-07-31T11:00:00Z',
      })
    )
    await waitFor(() => expect(screen.getAllByTestId('event-row')).toHaveLength(2))
    expect(
      within(screen.getAllByTestId('event-row')[0]!).getByText('Completed')
    ).toBeInTheDocument()
  })

  it('shows an empty events state', async () => {
    mockFetch(
      routes({
        'GET /api/v1/w/w1/tours/t1/events': () => ({
          body: makeEventsPage({ items: [], total: 0 }),
        }),
      })
    )
    renderAnalytics()

    expect(await screen.findByText(/No events yet/)).toBeInTheDocument()
  })

  it('offers a retry when the stats request fails', async () => {
    mockFetch(
      routes({
        'GET /api/v1/w/w1/tours/t1/stats': () => ({
          status: 500,
          body: { error: { code: 'server_error', message: 'boom' } },
        }),
      })
    )
    renderAnalytics()

    expect(await screen.findByText(/Could not load analytics/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('gates the page on tours:read', async () => {
    seedAuth(['conversations:read'])
    mockFetch(routes())
    renderAnalytics()

    expect(await screen.findByText(/don’t have access to tour analytics/)).toBeInTheDocument()
  })
})
