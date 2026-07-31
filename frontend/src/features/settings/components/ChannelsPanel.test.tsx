import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'

import type { Inbox } from '../api'
import { ChannelsPanel } from './ChannelsPanel'

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
        permissions: ['conversations:read', 'channels:manage'],
        workspace: { id: 'w1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

function inbox(overrides: Partial<Inbox> = {}): Inbox {
  return {
    id: 'ib1',
    name: 'WhatsApp support',
    channel_type: 'whatsapp',
    config: { phone_number_id: '123' },
    enabled: true,
    has_secrets: false,
    embed_snippet: null,
    widget_key: null,
    created_at: ISO,
    updated_at: ISO,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('ChannelsPanel', () => {
  it('lists the new channel types in the create dialog', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/w1/inboxes': () => ({ body: [] }) })

    renderApp(<ChannelsPanel />)
    await userEvent.click(await screen.findByRole('button', { name: /new channel/i }))

    const select = screen.getByLabelText('Channel type')
    const labels = Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
    for (const expected of ['WhatsApp', 'Facebook Messenger', 'Instagram', 'SMS (Twilio)', 'LINE']) {
      expect(labels).toContain(expected)
    }
  })

  it('renders whatsapp config fields, shows the webhook path, and PATCHes config + typed secrets', async () => {
    setupAuth()
    let patchBody: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/w1/inboxes': () => ({ body: [inbox()] }),
      'PATCH /api/v1/w/w1/inboxes/ib1': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: inbox({ has_secrets: true }) }
      },
    })

    renderApp(<ChannelsPanel />)
    await userEvent.click(await screen.findByRole('button', { name: /configure whatsapp support/i }))

    // Config prefilled from inbox.config; webhook path hint shown.
    expect(screen.getByLabelText(/phone number id/i)).toHaveValue('123')
    expect(screen.getByText('/api/channels/whatsapp/webhook/ib1')).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText(/webhook verify token/i), 'verify-tok')
    await userEvent.type(screen.getByLabelText(/access token/i), 'wa-secret')
    await userEvent.click(screen.getByRole('button', { name: /save configuration/i }))

    await waitFor(() => expect(patchBody).not.toBeNull())
    expect(patchBody!).toEqual({
      config: { phone_number_id: '123', webhook_verify_token: 'verify-tok' },
      secrets: { api_key: 'wa-secret' },
    })
  })

  it('omits secrets from the PATCH when none were typed (already configured)', async () => {
    setupAuth()
    let patchBody: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/w1/inboxes': () => ({
        body: [
          inbox({
            has_secrets: true,
            config: { phone_number_id: '123', webhook_verify_token: 'tok' },
          }),
        ],
      }),
      'PATCH /api/v1/w/w1/inboxes/ib1': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: inbox({ has_secrets: true }) }
      },
    })

    renderApp(<ChannelsPanel />)
    await userEvent.click(await screen.findByRole('button', { name: /configure whatsapp support/i }))

    // Secret inputs stay blank with an "unchanged" placeholder.
    expect(screen.getByLabelText(/access token/i)).toHaveAttribute('placeholder', 'unchanged')

    await userEvent.click(screen.getByRole('button', { name: /save configuration/i }))
    await waitFor(() => expect(patchBody).not.toBeNull())
    expect(patchBody!).toEqual({
      config: { phone_number_id: '123', webhook_verify_token: 'tok' },
    })
    expect(patchBody!).not.toHaveProperty('secrets')
  })

  it('blocks the first save until required secrets are provided', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/w1/inboxes': () => ({
        body: [inbox({ config: { phone_number_id: '1', webhook_verify_token: 't' } })],
      }),
    })

    renderApp(<ChannelsPanel />)
    await userEvent.click(await screen.findByRole('button', { name: /configure whatsapp support/i }))
    // has_secrets=false and required access token empty → save disabled.
    expect(screen.getByRole('button', { name: /save configuration/i })).toBeDisabled()
  })
})
