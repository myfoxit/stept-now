import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Billing } from '@/features/billing/api'
import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'

import { assignLocation } from './integrations/redirect'
import { BillingPanel } from './BillingPanel'

vi.mock('./integrations/redirect', () => ({ assignLocation: vi.fn() }))

function setupAuth(permissions: string[] = ['workspace:manage']) {
  useAuthStore.setState({
    accessToken: 't',
    user: { id: 'u1', email: 'me@stept.co', name: 'Me' },
    workspaceId: 'w1',
    bootstrapped: true,
    memberships: [
      {
        id: 'me',
        role: 'owner',
        is_available: true,
        permissions,
        workspace: { id: 'w1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

function billing(overrides: Partial<Billing> = {}): Billing {
  return {
    plan: 'free',
    status: 'active',
    seats: 1,
    member_count: 1,
    current_period_end: null,
    cancel_at_period_end: false,
    ai_runs_this_period: 0,
    included_ai_runs: 0,
    billing_enabled: true,
    publishable_key: 'pk_test_123',
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('BillingPanel', () => {
  it('shows the disabled notice on self-hosted instances (billing_enabled false)', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/w1/billing': () => ({ body: billing({ billing_enabled: false }) }),
    })

    renderApp(<BillingPanel />)
    expect(
      await screen.findByText(/billing is not enabled on this instance/i)
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /upgrade/i })).not.toBeInTheDocument()
  })

  it('starts a checkout for the chosen plan and hands the browser to Stripe', async () => {
    setupAuth()
    let postBody: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/w1/billing': () => ({ body: billing() }),
      'POST /api/v1/w/w1/billing/checkout-session': (init) => {
        postBody = JSON.parse(init!.body as string)
        return { body: { url: 'https://checkout.stripe.com/c/cs_123' } }
      },
    })

    renderApp(<BillingPanel />)
    await userEvent.click(await screen.findByRole('button', { name: /upgrade to cloud/i }))
    await waitFor(() => expect(postBody).not.toBeNull())
    expect(postBody!).toEqual({ plan: 'cloud' })
    await waitFor(() =>
      expect(vi.mocked(assignLocation)).toHaveBeenCalledWith('https://checkout.stripe.com/c/cs_123')
    )
  })

  it('renders the paid-plan state: usage meter, seats, renewal and the portal button', async () => {
    setupAuth()
    let portalCalled = false
    mockFetch({
      'GET /api/v1/w/w1/billing': () => ({
        body: billing({
          plan: 'cloud',
          seats: 3,
          member_count: 3,
          ai_runs_this_period: 123,
          included_ai_runs: 500,
          current_period_end: '2026-09-21T15:33:20Z',
        }),
      }),
      'POST /api/v1/w/w1/billing/portal-session': () => {
        portalCalled = true
        return { body: { url: 'https://billing.stripe.com/p/session_1' } }
      },
    })

    renderApp(<BillingPanel />)
    expect(await screen.findByText('123 / 500 AI runs this month')).toBeInTheDocument()
    expect(screen.getByText(/3 paid seats/)).toBeInTheDocument()
    expect(screen.getByText(/renews/i)).toBeInTheDocument()
    expect(screen.getByText('Current plan')).toBeInTheDocument()
    // Current plan gets no upgrade button; the other paid plan does.
    expect(screen.queryByRole('button', { name: /upgrade to cloud/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upgrade to business/i })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /manage billing/i }))
    await waitFor(() => expect(portalCalled).toBe(true))
    await waitFor(() =>
      expect(vi.mocked(assignLocation)).toHaveBeenCalledWith('https://billing.stripe.com/p/session_1')
    )
  })

  it('hides upgrade and portal actions without workspace:manage', async () => {
    setupAuth(['conversations:read'])
    mockFetch({
      'GET /api/v1/w/w1/billing': () => ({ body: billing({ plan: 'cloud' }) }),
    })

    renderApp(<BillingPanel />)
    expect(await screen.findByText('Current plan')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /upgrade/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /manage billing/i })).not.toBeInTheDocument()
  })

  it('toasts the ?checkout=success landing and drops the param', async () => {
    setupAuth()
    const success = vi.spyOn(toast, 'success')
    mockFetch({ 'GET /api/v1/w/w1/billing': () => ({ body: billing({ plan: 'cloud' }) }) })

    renderApp(<BillingPanel />, { route: '/settings/billing?checkout=success' })
    await waitFor(() => expect(success).toHaveBeenCalledWith('Subscription updated'))
  })
})
