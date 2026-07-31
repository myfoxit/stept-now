import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'

import type { Macro } from '../api'
import { MacrosTab } from './MacrosTab'

const ISO = '2026-01-01T00:00:00Z'

function setupAuth(
  permissions: string[] = ['conversations:read', 'conversations:write', 'conversations:manage']
) {
  useAuthStore.setState({
    accessToken: 't',
    user: { id: 'u1', email: 'me@stept.co', name: 'Me' },
    workspaceId: 'w1',
    bootstrapped: true,
    memberships: [
      {
        id: 'me',
        role: 'admin',
        is_available: true,
        permissions,
        workspace: { id: 'w1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

function macro(overrides: Partial<Macro> = {}): Macro {
  return {
    id: 'mc1',
    name: 'Close & thank',
    visibility: 'global',
    actions: [
      { type: 'send_reply', params: { content: 'Thanks!' } },
      { type: 'set_status', params: { status: 'resolved' } },
    ],
    created_by: 'u1',
    created_at: ISO,
    updated_at: ISO,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('MacrosTab', () => {
  it('lists macros with visibility badge and action summary', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/w1/macros': () => ({ body: [macro()] }) })

    renderApp(<MacrosTab />)
    expect(await screen.findByText('Close & thank')).toBeInTheDocument()
    expect(screen.getByText('Global')).toHaveAttribute('data-variant', 'default')
    expect(screen.getByText(/send reply → set status/i)).toBeInTheDocument()
  })

  it('serializes action rows into the POST body from the editor', async () => {
    setupAuth()
    let postBody: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/w1/macros': () => ({ body: [] }),
      'POST /api/v1/w/w1/macros': (init) => {
        postBody = JSON.parse(init!.body as string)
        return { status: 201, body: macro({ id: 'mc2', name: 'Resolve' }) }
      },
    })

    renderApp(<MacrosTab />)
    await userEvent.click(await screen.findByRole('button', { name: /new macro/i }))
    await userEvent.type(screen.getByLabelText('Name'), 'Resolve')

    // Default row is set_status; pick the status param, then add a remove_tag row.
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'resolved')
    await userEvent.click(screen.getByRole('button', { name: /add action/i }))
    const typeSelects = screen.getAllByLabelText('Action')
    await userEvent.selectOptions(typeSelects[1]!, 'remove_tag')
    await userEvent.type(screen.getByLabelText('Tag name'), 'vip')

    await userEvent.click(screen.getByRole('button', { name: /create macro/i }))
    await waitFor(() => expect(postBody).not.toBeNull())
    expect(postBody!).toEqual({
      name: 'Resolve',
      visibility: 'personal',
      actions: [
        { type: 'set_status', params: { status: 'resolved' } },
        { type: 'remove_tag', params: { tag: 'vip' } },
      ],
    })
  })

  it('disables the global visibility option without conversations:manage', async () => {
    setupAuth(['conversations:read', 'conversations:write'])
    mockFetch({ 'GET /api/v1/w/w1/macros': () => ({ body: [] }) })

    renderApp(<MacrosTab />)
    await userEvent.click(await screen.findByRole('button', { name: /new macro/i }))
    const global = screen.getByRole('option', { name: /global/i })
    expect(global).toBeDisabled()
  })
})
