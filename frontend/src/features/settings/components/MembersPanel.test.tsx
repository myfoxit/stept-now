import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'

import { MembersPanel } from './MembersPanel'

const ISO = '2026-01-01T00:00:00Z'

function setupAuth(permissions: string[]) {
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

const member = {
  id: 'm2',
  workspace_id: 'w1',
  role: 'agent',
  custom_role_id: null,
  is_available: true,
  user: { id: 'u2', email: 'bob@stept.co', name: 'Bob', created_at: ISO },
  created_at: ISO,
}

const baseRoutes = {
  'GET /api/v1/w/w1/members': () => ({ body: [member] }),
  'GET /api/v1/w/w1/roles': () => ({ body: [] }),
  'GET /api/v1/w/w1/invitations': () => ({ body: [] }),
}

afterEach(() => {
  cleanup() // unmount before mutating the store so no live component re-renders without a workspace
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('MembersPanel', () => {
  it('changes a member role via PATCH when the user has members:manage', async () => {
    setupAuth(['members:manage'])
    let patchBody: unknown
    mockFetch({
      ...baseRoutes,
      'PATCH /api/v1/w/w1/members/m2': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: { ...member, role: 'admin' } }
      },
    })

    renderApp(<MembersPanel />)
    const select = await screen.findByLabelText('Role for Bob')
    await userEvent.selectOptions(select, 'admin')

    await waitFor(() => expect(patchBody).toEqual({ role: 'admin' }))
  })

  it('hides role editing and invite when the user lacks members:manage', async () => {
    setupAuth(['contacts:read'])
    mockFetch(baseRoutes)

    renderApp(<MembersPanel />)
    // The member row still renders (read-only)…
    expect(await screen.findByText('Bob')).toBeInTheDocument()
    // …but no role <select>, no invite form, no remove button.
    expect(screen.queryByLabelText('Role for Bob')).not.toBeInTheDocument()
    expect(screen.queryByText(/invite a teammate/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /remove bob/i })).not.toBeInTheDocument()
  })
})
