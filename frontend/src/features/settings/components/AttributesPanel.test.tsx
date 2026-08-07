import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'
import { AttributesPanel } from '@/features/settings/components/AttributesPanel'

const ISO = '2026-01-01T00:00:00Z'

function setupAuth(permissions = ['contacts:read', 'workspace:manage']) {
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

const MRR = {
  id: 'a1',
  attribute_model: 'contact',
  key: 'mrr',
  display_name: 'Monthly revenue',
  description: null,
  attribute_type: 'currency',
  options: [],
  default_value: null,
  regex_pattern: null,
  regex_cue: null,
  ord: 0,
  shown_on_front: true,
  created_at: ISO,
  updated_at: ISO,
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('AttributesPanel', () => {
  it('lists the definitions for the selected model', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/ws1/custom-attributes': () => ({ body: [MRR] }) })
    renderApp(<AttributesPanel />)

    expect(await screen.findByText('Monthly revenue')).toBeInTheDocument()
    expect(screen.getByText('mrr')).toBeInTheDocument()
    expect(screen.getByText('currency')).toBeInTheDocument()
  })

  it('explains that free-form values still work when empty', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/ws1/custom-attributes': () => ({ body: [] }) })
    renderApp(<AttributesPanel />)
    expect(await screen.findByText(/free-form values keep working/i)).toBeInTheDocument()
  })

  it('creates a definition with the model it is scoped to', async () => {
    setupAuth()
    let body: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/ws1/custom-attributes': () => ({ body: [] }),
      'POST /api/v1/w/ws1/custom-attributes': (init) => {
        body = JSON.parse(String(init?.body))
        return { body: MRR, status: 201 }
      },
    })
    renderApp(<AttributesPanel />)

    await userEvent.click(await screen.findByRole('button', { name: /new attribute/i }))
    await userEvent.type(screen.getByLabelText('Display name'), 'Monthly revenue')
    await userEvent.type(screen.getByLabelText('Key'), 'mrr')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(body).not.toBeNull())
    expect(body!.attribute_model).toBe('contact')
    expect(body!.key).toBe('mrr')
  })

  it('requires options before saving a list attribute', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/ws1/custom-attributes': () => ({ body: [] }) })
    renderApp(<AttributesPanel />)

    await userEvent.click(await screen.findByRole('button', { name: /new attribute/i }))
    await userEvent.type(screen.getByLabelText('Display name'), 'Plan')
    await userEvent.type(screen.getByLabelText('Key'), 'plan')
    await userEvent.click(screen.getByLabelText('Type'))
    await userEvent.click(await screen.findByRole('option', { name: 'list' }))

    expect(await screen.findByText(/needs an option/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled()
  })

  it('locks the key when editing an existing definition', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/ws1/custom-attributes': () => ({ body: [MRR] }) })
    renderApp(<AttributesPanel />)

    await userEvent.click(await screen.findByRole('button', { name: /edit monthly revenue/i }))
    expect(screen.getByLabelText('Key')).toBeDisabled()
  })

  it('hides mutations without workspace:manage', async () => {
    setupAuth(['contacts:read'])
    mockFetch({ 'GET /api/v1/w/ws1/custom-attributes': () => ({ body: [MRR] }) })
    renderApp(<AttributesPanel />)

    await screen.findByText('Monthly revenue')
    expect(screen.queryByRole('button', { name: /new attribute/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /edit monthly revenue/i })).toBeNull()
  })
})
