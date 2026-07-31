import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { makeStats, makeTour, resetAuth, seedAuth } from '../test-utils'
import { Component as ToursPage } from './ToursPage'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

const tours = [
  makeTour({ id: 't1', name: 'Welcome tour', kind: 'flow', status: 'live' }),
  makeTour({
    id: 't2',
    name: "What's new",
    kind: 'banner',
    status: 'live',
    settings: { mode: 'driven', backdrop: true, show_progress: true, dismissable: true },
  }),
  makeTour({ id: 't3', name: 'Beta invite', kind: 'announcement', status: 'draft' }),
]

const statsRoutes = {
  'GET /api/v1/w/w1/tours/t1/stats': () => ({ body: makeStats() }),
  'GET /api/v1/w/w1/tours/t2/stats': () => ({ body: makeStats() }),
  'GET /api/v1/w/w1/tours/t3/stats': () => ({ body: makeStats() }),
}

beforeEach(() => seedAuth())
afterEach(() => {
  cleanup()
  resetAuth()
})

describe('ToursPage', () => {
  it('filters the list by kind tab', async () => {
    mockFetch({ 'GET /api/v1/w/w1/tours': () => ({ body: tours }), ...statsRoutes })
    renderApp(<ToursPage />)

    expect(await screen.findByText('Welcome tour')).toBeInTheDocument()
    expect(screen.getAllByTestId('tour-card')).toHaveLength(3)

    await userEvent.click(screen.getByRole('tab', { name: 'Banners' }))
    expect(screen.getAllByTestId('tour-card')).toHaveLength(1)
    expect(screen.getByText("What's new")).toBeInTheDocument()
    expect(screen.queryByText('Welcome tour')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: 'Announcements' }))
    expect(screen.getByText('Beta invite')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: 'Flows' }))
    expect(screen.getAllByTestId('tour-card')).toHaveLength(1)
    expect(screen.getByText('Welcome tour')).toBeInTheDocument()
  })

  it('shows kind and driven-mode badges and links each card to its analytics', async () => {
    mockFetch({ 'GET /api/v1/w/w1/tours': () => ({ body: tours }), ...statsRoutes })
    renderApp(<ToursPage />)

    const banner = within((await screen.findAllByTestId('tour-card'))[1]!)
    expect(banner.getByText('Banner')).toBeInTheDocument()
    expect(banner.getByText('Do it for me')).toBeInTheDocument()
    expect(banner.getByRole('link', { name: /analytics/i })).toHaveAttribute(
      'href',
      '/tours/t2/analytics'
    )
  })

  it('duplicates a tour as a named copy', async () => {
    let posted: Record<string, unknown> | undefined
    mockFetch({
      'GET /api/v1/w/w1/tours': () => ({ body: tours }),
      ...statsRoutes,
      'POST /api/v1/w/w1/tours': (init) => {
        posted = JSON.parse(init!.body as string)
        return { status: 201, body: makeTour({ id: 't4', name: 'Welcome tour (copy)' }) }
      },
    })
    renderApp(<ToursPage />)

    await userEvent.click(await screen.findByRole('button', { name: /duplicate welcome tour/i }))

    await waitFor(() => expect(posted).toBeDefined())
    expect(posted!.name).toBe('Welcome tour (copy)')
    expect(posted!.kind).toBe('flow')
    expect(posted!.steps).toHaveLength(1)
  })

  it('creates a tour of the picked kind', async () => {
    let posted: Record<string, unknown> | undefined
    mockFetch({
      'GET /api/v1/w/w1/tours': () => ({ body: [] }),
      'POST /api/v1/w/w1/tours': (init) => {
        posted = JSON.parse(init!.body as string)
        return { status: 201, body: makeTour({ id: 't9', name: 'Launch banner', kind: 'banner' }) }
      },
    })
    renderApp(<ToursPage />)

    await userEvent.click(await screen.findByRole('button', { name: /new tour/i }))
    await userEvent.type(screen.getByLabelText('Name'), 'Launch banner')
    await userEvent.selectOptions(screen.getByLabelText('Kind'), 'banner')
    await userEvent.click(screen.getByRole('button', { name: /create tour/i }))

    await waitFor(() => expect(posted).toBeDefined())
    expect(posted).toMatchObject({ name: 'Launch banner', kind: 'banner' })
  })

  it('hides create and duplicate without tours:manage', async () => {
    seedAuth(['tours:read'])
    mockFetch({ 'GET /api/v1/w/w1/tours': () => ({ body: tours }), ...statsRoutes })
    renderApp(<ToursPage />)

    await screen.findByText('Welcome tour')
    expect(screen.queryByRole('button', { name: /new tour/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /duplicate/i })).not.toBeInTheDocument()
    // Read-only users keep the analytics entry point.
    expect(screen.getAllByRole('link', { name: /analytics/i })).toHaveLength(3)
  })

  it('renders an error state with retry', async () => {
    mockFetch({
      'GET /api/v1/w/w1/tours': () => ({
        status: 500,
        body: { error: { code: 'server_error', message: 'boom' } },
      }),
    })
    renderApp(<ToursPage />)

    expect(await screen.findByText(/Could not load tours/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('shows a kind-specific empty state', async () => {
    mockFetch({
      'GET /api/v1/w/w1/tours': () => ({ body: [makeTour({ id: 't1', kind: 'flow' })] }),
      ...statsRoutes,
    })
    renderApp(<ToursPage />)

    await screen.findByTestId('tour-card')
    await userEvent.click(screen.getByRole('tab', { name: 'Banners' }))
    expect(screen.getByText('No banners yet')).toBeInTheDocument()
  })
})
