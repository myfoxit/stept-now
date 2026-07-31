import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'
import { SlaCard } from '@/features/inbox/components/SlaCard'
import type { ConversationSla, SlaPolicy } from '@/features/inbox/api'

const ISO = '2026-01-01T00:00:00Z'

function setupAuth(permissions: string[] = ['conversations:read', 'conversations:manage']) {
  useAuthStore.setState({
    accessToken: 't',
    user: { id: 'u1', email: 'me@stept.co', name: 'Me' },
    workspaceId: 'ws1',
    bootstrapped: true,
    memberships: [
      {
        id: 'me',
        role: 'admin',
        is_available: true,
        permissions,
        workspace: { id: 'ws1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

function policy(overrides: Partial<SlaPolicy> = {}): SlaPolicy {
  return {
    id: 'p1',
    name: 'Premium',
    description: null,
    first_response_minutes: 15,
    next_response_minutes: null,
    resolution_minutes: null,
    created_at: ISO,
    updated_at: ISO,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

function mockSla(sla: ConversationSla, policies: SlaPolicy[] = [policy()]) {
  return {
    'GET /api/v1/w/ws1/conversations/c1/sla': () => ({ body: sla }),
    'GET /api/v1/w/ws1/slas': () => ({ body: policies }),
  }
}

describe('SlaCard', () => {
  it('shows the policy with a destructive badge and breach list when missed', async () => {
    setupAuth()
    mockFetch(
      mockSla({
        policy: policy(),
        status: 'missed',
        events: [{ id: 'e1', event_type: 'first_response_missed', created_at: ISO, meta: {} }],
      })
    )

    renderApp(<SlaCard conversationId="c1" />)
    expect(await screen.findByText('Premium', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getByText('missed')).toHaveAttribute('data-variant', 'destructive')
    expect(screen.getByTestId('sla-events')).toHaveTextContent(/first response missed/i)
  })

  it('uses secondary for active and a green default badge for hit', async () => {
    setupAuth()
    mockFetch(mockSla({ policy: policy(), status: 'active', events: [] }))
    renderApp(<SlaCard conversationId="c1" />)
    expect(await screen.findByText('active')).toHaveAttribute('data-variant', 'secondary')
    cleanup()

    mockFetch(mockSla({ policy: policy(), status: 'hit', events: [] }))
    renderApp(<SlaCard conversationId="c1" />)
    const hit = await screen.findByText('hit')
    expect(hit).toHaveAttribute('data-variant', 'default')
    expect(hit.className).toContain('emerald')
  })

  it('applies a policy via PUT and can remove it', async () => {
    setupAuth()
    let putBody: Record<string, unknown> | null = null
    mockFetch({
      ...mockSla({ policy: null, status: null, events: [] }, [policy(), policy({ id: 'p2', name: 'Standard' })]),
      'PUT /api/v1/w/ws1/conversations/c1/sla': (init) => {
        putBody = JSON.parse(init!.body as string)
        return { body: { policy: policy({ id: 'p2', name: 'Standard' }), status: 'active', events: [] } }
      },
    })

    renderApp(<SlaCard conversationId="c1" />)
    expect(await screen.findByText(/no sla applied/i)).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Apply SLA'), 'p2')
    await waitFor(() => expect(putBody).not.toBeNull())
    expect(putBody!).toEqual({ sla_policy_id: 'p2' })
  })

  it('hides the apply control without conversations:manage', async () => {
    setupAuth(['conversations:read'])
    mockFetch(mockSla({ policy: policy(), status: 'active', events: [] }))
    renderApp(<SlaCard conversationId="c1" />)
    expect(await screen.findByText('Premium')).toBeInTheDocument()
    expect(screen.queryByLabelText('Apply SLA')).not.toBeInTheDocument()
  })
})
