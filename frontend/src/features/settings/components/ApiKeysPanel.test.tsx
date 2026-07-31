import { cleanup, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'

import { ApiKeysPanel } from './ApiKeysPanel'

const ISO = '2026-01-01T00:00:00Z'

function setupAuth() {
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
        permissions: ['apikeys:manage'],
        workspace: { id: 'w1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

afterEach(() => {
  cleanup() // unmount before mutating the store so no live component re-renders without a workspace
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('ApiKeysPanel', () => {
  it('creates a key and reveals the secret exactly once', async () => {
    setupAuth()
    let postBody: unknown
    mockFetch({
      'GET /api/v1/w/w1/api-keys': () => ({ body: [] }),
      'POST /api/v1/w/w1/api-keys': (init) => {
        postBody = JSON.parse(init!.body as string)
        return {
          status: 201,
          body: {
            id: 'k1',
            name: 'CI token',
            prefix: 'sk_abcd',
            scopes: ['read'],
            last_used_at: null,
            revoked_at: null,
            created_at: ISO,
            key: 'sk_live_supersecret_value',
          },
        }
      },
    })

    renderApp(<ApiKeysPanel />)

    await userEvent.click(screen.getByRole('button', { name: /new key/i }))
    await userEvent.type(screen.getByLabelText('Name'), 'CI token')
    await userEvent.click(screen.getByRole('button', { name: /create key/i }))

    const revealed = await screen.findByTestId('revealed-key')
    expect(revealed).toHaveTextContent('sk_live_supersecret_value')
    expect(postBody).toEqual({ name: 'CI token', scopes: ['read'] })
  })
})
