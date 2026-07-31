import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { emailInbox, makeCampaign, resetAuth, seedAuth, widgetInbox } from '../test-utils'
import { Component as CampaignsPage } from './CampaignsPage'

const baseRoutes = {
  'GET /api/v1/w/w1/inboxes': () => ({ body: [widgetInbox, emailInbox] }),
}

beforeEach(() => seedAuth())
afterEach(() => {
  cleanup() // unmount before clearing the store so no live component re-renders without a workspace
  resetAuth()
})

describe('CampaignsPage', () => {
  it('renders ongoing and one_off rows with type badges, summaries and statuses', async () => {
    const scheduled = new Date(2026, 7, 2, 9, 0).toISOString()
    mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/campaigns': () => ({
        body: [
          makeCampaign({ id: 'c1', title: 'Pricing nudge', status: 'active', sent_count: 12 }),
          makeCampaign({
            id: 'c2',
            title: 'Summer promo',
            campaign_type: 'one_off',
            status: 'draft',
            inbox_id: 'i2',
            trigger_rules: {},
            scheduled_at: scheduled,
            audience: { type: 'all' },
          }),
        ],
      }),
    })
    renderApp(<CampaignsPage />, { route: '/campaigns' })

    expect(await screen.findByText('Pricing nudge')).toBeInTheDocument()
    expect(screen.getByText('In-app')).toBeInTheDocument()
    expect(screen.getByText('Scheduled')).toBeInTheDocument()
    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.getByText('Draft')).toBeInTheDocument()
    expect(screen.getByText(/On \/pricing\* after 30s/)).toBeInTheDocument()
    expect(screen.getByText(/12 sent/)).toBeInTheDocument()
    expect(screen.getByText(/Aug 2, 09:00/)).toBeInTheDocument()
    expect(screen.getByText(/Website widget · widget/)).toBeInTheDocument()
    expect(screen.getByText(/Support email · email/)).toBeInTheDocument()
  })

  it('shows processing and completed statuses and locks a processing campaign', async () => {
    mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/campaigns': () => ({
        body: [
          makeCampaign({
            id: 'c1',
            title: 'Sending now',
            campaign_type: 'one_off',
            status: 'processing',
            inbox_id: 'i2',
          }),
          makeCampaign({
            id: 'c2',
            title: 'Old blast',
            campaign_type: 'one_off',
            status: 'completed',
            inbox_id: 'i2',
          }),
        ],
      }),
    })
    renderApp(<CampaignsPage />, { route: '/campaigns' })

    expect(await screen.findByText('Processing')).toBeInTheDocument()
    expect(screen.getByText('Completed')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Enable Sending now' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Edit Sending now' })).toBeDisabled()
    // Neither processing nor completed campaigns can be activated or paused.
    expect(screen.queryByRole('button', { name: /Activate/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Pause/ })).not.toBeInTheDocument()
  })

  it('shows the empty state with a create CTA', async () => {
    mockFetch({ ...baseRoutes, 'GET /api/v1/w/w1/campaigns': () => ({ body: [] }) })
    renderApp(<CampaignsPage />, { route: '/campaigns' })

    expect(await screen.findByText('No campaigns yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Create your first campaign/ })).toBeInTheDocument()
  })

  it('shows an error state with retry when the list fails', async () => {
    let failed = false
    mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/campaigns': () => {
        if (!failed) {
          failed = true
          return { status: 500, body: { error: { code: 'boom', message: 'boom' } } }
        }
        return { body: [makeCampaign({ title: 'Recovered' })] }
      },
    })
    renderApp(<CampaignsPage />, { route: '/campaigns' })

    expect(await screen.findByText(/Could not load campaigns/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Recovered')).toBeInTheDocument()
  })

  it('hides the new-campaign button and disables the switch without automations:manage', async () => {
    seedAuth(['automations:read'])
    mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/campaigns': () => ({ body: [makeCampaign({ title: 'Pricing nudge' })] }),
    })
    renderApp(<CampaignsPage />, { route: '/campaigns' })

    expect(await screen.findByText('Pricing nudge')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /New campaign/ })).not.toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Enable Pricing nudge' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /Delete/ })).not.toBeInTheDocument()
  })

  it('toggles enabled via PATCH with the new value', async () => {
    let patchBody: unknown
    mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/campaigns': () => ({
        body: [makeCampaign({ id: 'c1', title: 'Pricing nudge', enabled: true })],
      }),
      'PATCH /api/v1/w/w1/campaigns/c1': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: makeCampaign({ id: 'c1', enabled: false }) }
      },
    })
    renderApp(<CampaignsPage />, { route: '/campaigns' })

    await userEvent.click(await screen.findByRole('switch', { name: 'Enable Pricing nudge' }))
    await waitFor(() => expect(patchBody).toEqual({ enabled: false }))
  })

  it('activates a draft campaign', async () => {
    const fetchFn = mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/campaigns': () => ({
        body: [makeCampaign({ id: 'c1', title: 'Pricing nudge', status: 'draft' })],
      }),
      'POST /api/v1/w/w1/campaigns/c1/activate': () => ({
        body: makeCampaign({ id: 'c1', status: 'active' }),
      }),
    })
    renderApp(<CampaignsPage />, { route: '/campaigns' })

    await userEvent.click(await screen.findByRole('button', { name: 'Activate Pricing nudge' }))
    await waitFor(() => {
      const calls = fetchFn.mock.calls.map(([url, init]) => `${init?.method} ${url}`)
      expect(calls).toContain('POST /api/v1/w/w1/campaigns/c1/activate')
    })
  })

  it('pauses an active campaign', async () => {
    const fetchFn = mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/campaigns': () => ({
        body: [makeCampaign({ id: 'c1', title: 'Pricing nudge', status: 'active' })],
      }),
      'POST /api/v1/w/w1/campaigns/c1/pause': () => ({
        body: makeCampaign({ id: 'c1', status: 'draft' }),
      }),
    })
    renderApp(<CampaignsPage />, { route: '/campaigns' })

    await userEvent.click(await screen.findByRole('button', { name: 'Pause Pricing nudge' }))
    await waitFor(() => {
      const calls = fetchFn.mock.calls.map(([url, init]) => `${init?.method} ${url}`)
      expect(calls).toContain('POST /api/v1/w/w1/campaigns/c1/pause')
    })
  })

  it('deletes a campaign only after confirming', async () => {
    const fetchFn = mockFetch({
      ...baseRoutes,
      'GET /api/v1/w/w1/campaigns': () => ({
        body: [makeCampaign({ id: 'c1', title: 'Pricing nudge' })],
      }),
      'DELETE /api/v1/w/w1/campaigns/c1': () => ({ body: { message: 'deleted' } }),
    })
    renderApp(<CampaignsPage />, { route: '/campaigns' })

    await userEvent.click(await screen.findByRole('button', { name: 'Delete Pricing nudge' }))
    expect(await screen.findByText(/cannot be undone/)).toBeInTheDocument()
    // Nothing deleted until the dialog is confirmed.
    expect(
      fetchFn.mock.calls.some(([, init]) => init?.method === 'DELETE')
    ).toBe(false)

    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => {
      const calls = fetchFn.mock.calls.map(([url, init]) => `${init?.method} ${url}`)
      expect(calls).toContain('DELETE /api/v1/w/w1/campaigns/c1')
    })
  })
})
