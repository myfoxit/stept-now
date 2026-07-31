import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'
import { useAuthStore } from '@/stores/auth'
import { Component as ContactsPage } from '@/features/contacts/pages/ContactsPage'

function makeContact(id: string, name: string, email: string) {
  return {
    id,
    external_id: null,
    email,
    name,
    phone: null,
    avatar_url: null,
    attributes: { plan: 'enterprise' },
    verified: true,
    first_seen_at: null,
    last_seen_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    tags: [{ id: 't1', name: 'vip', color: '#f00', created_at: new Date().toISOString() }],
  }
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: 't',
    user: { id: 'u1', email: 'r@stept.co', name: 'Reggie' },
    memberships: [],
    workspaceId: 'ws1',
    bootstrapped: true,
  })
})

describe('ContactsPage', () => {
  it('renders a table of contacts with tags and plan', async () => {
    mockFetch({
      'GET /api/v1/w/ws1/contacts': () => ({
        body: { items: [makeContact('c1', 'Grace Hopper', 'grace@navy.mil')], next_cursor: null },
      }),
      'GET /api/v1/w/ws1/segments': () => ({ body: [] }),
    })
    renderApp(<ContactsPage />, { route: '/contacts' })
    expect(await screen.findByText('Grace Hopper')).toBeInTheDocument()
    expect(screen.getByText('grace@navy.mil')).toBeInTheDocument()
    expect(screen.getByText('vip')).toBeInTheDocument()
    expect(screen.getByText('enterprise')).toBeInTheDocument()
  })

  it('shows an empty state when there are no contacts', async () => {
    mockFetch({
      'GET /api/v1/w/ws1/contacts': () => ({ body: { items: [], next_cursor: null } }),
      'GET /api/v1/w/ws1/segments': () => ({ body: [] }),
    })
    renderApp(<ContactsPage />, { route: '/contacts' })
    expect(await screen.findByText(/no contacts found/i)).toBeInTheDocument()
  })
})
