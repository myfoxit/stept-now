import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'

import { McpPanel } from './McpPanel'

const ISO = '2026-01-01T00:00:00Z'
const CLAUDE_CODE_RAW =
  'claude mcp add --transport http stept http://localhost:8600/mcp --header "Authorization: Bearer sk_stept_rawsecret"'
const CLAUDE_CODE_PLACEHOLDER =
  'claude mcp add --transport http stept http://localhost:8600/mcp --header "Authorization: Bearer YOUR_STEPT_KEY"'

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

function createdKey(body: { name: string; scopes: string[] }) {
  return {
    id: 'k9',
    name: body.name,
    prefix: 'sk_ab12',
    scopes: body.scopes,
    agent_id: null,
    last_used_at: null,
    revoked_at: null,
    created_at: ISO,
    key: 'sk_stept_rawsecret',
  }
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('McpPanel', () => {
  it('one-click creates a key named after the client and fills the raw key into the snippet', async () => {
    setupAuth()
    let postBody: { name: string; scopes: string[] } | undefined
    mockFetch({
      'GET /api/v1/w/w1/api-keys': () => ({ body: [] }),
      'POST /api/v1/w/w1/api-keys': (init) => {
        postBody = JSON.parse(init!.body as string)
        return { status: 201, body: createdKey(postBody!) }
      },
    })
    renderApp(<McpPanel />)

    await userEvent.click(screen.getByRole('button', { name: /create key for this client/i }))

    await waitFor(() => expect(postBody).toEqual({ name: 'Claude Code', scopes: ['read', 'write'] }))
    expect(await screen.findByText(CLAUDE_CODE_RAW)).toBeInTheDocument()
    expect(screen.getByText(/shown once — copy it now/)).toBeInTheDocument()

    // Done clears the raw key from state: snippets fall back to the placeholder.
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByText(CLAUDE_CODE_RAW)).not.toBeInTheDocument()
    expect(screen.getByText(CLAUDE_CODE_PLACEHOLDER)).toBeInTheDocument()
    expect(screen.queryByText(/shown once — copy it now/)).not.toBeInTheDocument()
  })

  it('derives the key name from the selected client tab', async () => {
    setupAuth()
    let postBody: { name: string; scopes: string[] } | undefined
    mockFetch({
      'GET /api/v1/w/w1/api-keys': () => ({ body: [] }),
      'POST /api/v1/w/w1/api-keys': (init) => {
        postBody = JSON.parse(init!.body as string)
        return { status: 201, body: createdKey(postBody!) }
      },
    })
    renderApp(<McpPanel />)

    await userEvent.click(screen.getByRole('tab', { name: 'Cursor' }))
    await userEvent.click(screen.getByRole('button', { name: /create key for this client/i }))

    await waitFor(() => expect(postBody).toEqual({ name: 'Cursor', scopes: ['read', 'write'] }))
  })

  it('lets the customize popover adjust name and scopes before creating', async () => {
    setupAuth()
    let postBody: { name: string; scopes: string[] } | undefined
    mockFetch({
      'GET /api/v1/w/w1/api-keys': () => ({ body: [] }),
      'POST /api/v1/w/w1/api-keys': (init) => {
        postBody = JSON.parse(init!.body as string)
        return { status: 201, body: createdKey(postBody!) }
      },
    })
    renderApp(<McpPanel />)

    await userEvent.click(screen.getByRole('button', { name: /customize key before creating/i }))
    const name = await screen.findByLabelText('Key name')
    await userEvent.clear(name)
    await userEvent.type(name, 'Laptop key')
    await userEvent.click(screen.getByRole('checkbox', { name: /write/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))

    await waitFor(() => expect(postBody).toEqual({ name: 'Laptop key', scopes: ['read'] }))
    expect(await screen.findByText(CLAUDE_CODE_RAW)).toBeInTheDocument()
  })

  it('marks agent-bound keys in the table', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/w1/api-keys': () => ({
        body: [
          {
            id: 'k1',
            name: 'Cursor · Sage',
            prefix: 'sk_1111',
            scopes: ['read', 'write'],
            agent_id: 'a1',
            last_used_at: null,
            revoked_at: null,
            created_at: ISO,
          },
          {
            id: 'k2',
            name: 'Zapier',
            prefix: 'sk_2222',
            scopes: ['read'],
            agent_id: null,
            last_used_at: null,
            revoked_at: null,
            created_at: ISO,
          },
        ],
      }),
    })
    renderApp(<McpPanel />)

    expect(await screen.findByText('Cursor · Sage')).toBeInTheDocument()
    expect(screen.getAllByText('agent-bound')).toHaveLength(1)
    expect(screen.getByText('Zapier')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Revoke' })).toHaveLength(2)
  })
})
