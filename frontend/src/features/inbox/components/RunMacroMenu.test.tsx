import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'
import { RunMacroMenu } from '@/features/inbox/components/RunMacroMenu'

vi.mock('sonner', () => {
  const toastFn = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() })
  return { toast: toastFn, Toaster: () => null }
})

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

const MACROS = [
  {
    id: 'mc1',
    name: 'Escalate',
    visibility: 'global',
    actions: [{ type: 'set_priority', params: { priority: 'urgent' } }],
    created_by: null,
    created_at: ISO,
    updated_at: ISO,
  },
]

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
  vi.mocked(toast.error).mockClear()
})

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('RunMacroMenu', () => {
  it('lists macros and POSTs the run with the conversation id', async () => {
    setupAuth()
    let runBody: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/ws1/macros': () => ({ body: MACROS }),
      'POST /api/v1/w/ws1/macros/mc1/run': (init) => {
        runBody = JSON.parse(init!.body as string)
        return { body: { results: [{ action: 'set_priority', ok: true, error: null }] } }
      },
    })

    renderApp(<RunMacroMenu conversationId="c1" />)
    await userEvent.click(screen.getByRole('button', { name: /run macro/i }))
    await userEvent.click(await screen.findByText('Escalate'))

    await waitFor(() => expect(runBody).not.toBeNull())
    expect(runBody!).toEqual({ conversation_id: 'c1' })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Macro applied'))
  })

  it('shows a partial-failure toast listing the failed actions', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/ws1/macros': () => ({ body: MACROS }),
      'POST /api/v1/w/ws1/macros/mc1/run': () => ({
        body: {
          results: [
            { action: 'assign_user', ok: false, error: 'member not found' },
            { action: 'set_priority', ok: true, error: null },
          ],
        },
      }),
    })

    renderApp(<RunMacroMenu conversationId="c1" />)
    await userEvent.click(screen.getByRole('button', { name: /run macro/i }))
    await userEvent.click(await screen.findByText('Escalate'))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Macro partially applied',
        expect.objectContaining({
          description: expect.stringContaining('assign_user (member not found)'),
        })
      )
    )
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('renders nothing without conversations:manage', () => {
    setupAuth(['conversations:read'])
    mockFetch({})
    renderApp(<RunMacroMenu conversationId="c1" />)
    expect(screen.queryByRole('button', { name: /run macro/i })).not.toBeInTheDocument()
  })
})
