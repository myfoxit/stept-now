import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'

import type { SlaPolicy } from '../api'
import { SlaPanel } from './SlaPanel'

const ISO = '2026-01-01T00:00:00Z'

function setupAuth(permissions: string[] = ['conversations:read', 'automations:manage']) {
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

function policy(overrides: Partial<SlaPolicy> = {}): SlaPolicy {
  return {
    id: 'p1',
    name: 'Premium',
    description: 'VIP customers',
    first_response_minutes: 15,
    next_response_minutes: null,
    resolution_minutes: 240,
    created_at: ISO,
    updated_at: ISO,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('SlaPanel', () => {
  it('lists policies with their threshold summary', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/w1/slas': () => ({ body: [policy()] }) })

    renderApp(<SlaPanel />)
    expect(await screen.findByText('Premium')).toBeInTheDocument()
    expect(screen.getByText(/first response 15m/i)).toBeInTheDocument()
    expect(screen.getByText(/resolution 4h/i)).toBeInTheDocument()
  })

  it('keeps create disabled with an error until at least one threshold is set, then POSTs', async () => {
    setupAuth()
    let postBody: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/w1/slas': () => ({ body: [] }),
      'POST /api/v1/w/w1/slas': (init) => {
        postBody = JSON.parse(init!.body as string)
        return { status: 201, body: policy({ name: 'Standard' }) }
      },
    })

    renderApp(<SlaPanel />)
    await userEvent.click(await screen.findByRole('button', { name: /new policy/i }))
    await userEvent.type(screen.getByLabelText('Name'), 'Standard')

    // Name alone is not enough — no thresholds yet.
    expect(screen.getByText(/set at least one target time/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create policy/i })).toBeDisabled()

    await userEvent.type(screen.getByLabelText(/first response/i), '30')
    expect(screen.queryByText(/set at least one target time/i)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /create policy/i }))
    await waitFor(() => expect(postBody).not.toBeNull())
    expect(postBody!).toEqual({
      name: 'Standard',
      description: null,
      first_response_minutes: 30,
      next_response_minutes: null,
      resolution_minutes: null,
    })
  })

  it('hides mutations without automations:manage', async () => {
    setupAuth(['conversations:read'])
    mockFetch({ 'GET /api/v1/w/w1/slas': () => ({ body: [policy()] }) })

    renderApp(<SlaPanel />)
    expect(await screen.findByText('Premium')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /new policy/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /delete premium/i })).not.toBeInTheDocument()
  })
})
