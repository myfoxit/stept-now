import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'

import type { Inbox } from '../api'
import { EmailInboxWizard } from './EmailInboxWizard'

const ISO = '2026-08-01T00:00:00Z'

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
        permissions: ['channels:manage', 'integrations:manage'],
        workspace: { id: 'w1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

const googleProvider = {
  id: 'google',
  name: 'Google (Gmail & Drive)',
  category: 'email',
  auth: 'oauth2',
  description: '',
  doc_slug: 'google',
  configured: true,
  connections: [
    {
      id: 'conn-g1',
      provider: 'google',
      status: 'connected',
      account_label: 'support@acme.com',
      scopes: [],
      meta: {},
      created_at: ISO,
    },
  ],
  credential: null,
}

const emptyIntegrations = { providers: [] }

function createdInbox(config: Record<string, unknown>): Inbox {
  return {
    id: 'nb1',
    name: 'Support email',
    channel_type: 'email',
    enabled: true,
    has_secrets: true,
    embed_snippet: null,
    widget_key: null,
    created_at: ISO,
    updated_at: ISO,
    config: {
      forward_to: 'in-abc123def456@in.stept.dev',
      webhook_token: 'tok123',
      ...config,
    },
  } as unknown as Inbox
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('EmailInboxWizard — transport picker', () => {
  it('offers every transport; Gmail/Microsoft without a connection deep-link to integrations', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({ body: emptyIntegrations }),
    })

    renderApp(<EmailInboxWizard open onOpenChange={() => {}} defaultName="Support email" />)

    for (const label of [
      'Gmail',
      'Microsoft 365',
      'SMTP / IMAP',
      'Amazon SES',
      'Resend',
      'Postmark',
      'SendGrid',
      'Mailgun',
      'Forwarding only',
    ]) {
      expect(await screen.findByText(label)).toBeInTheDocument()
    }
    // No google/microsoft connection → the card is a deep link, not a choice.
    expect(screen.getByRole('link', { name: /connect google first/i })).toHaveAttribute(
      'href',
      '/settings/integrations'
    )
    expect(screen.getByRole('link', { name: /connect microsoft first/i })).toBeInTheDocument()
    // The unconnected one-click cards are not selectable at all.
    expect(screen.queryByRole('button', { name: /^gmail/i })).not.toBeInTheDocument()
  })

  it('a connected Google account makes Gmail selectable with a connection select on step 2', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({ body: { providers: [googleProvider] } }),
    })

    renderApp(<EmailInboxWizard open onOpenChange={() => {}} defaultName="Support email" />)

    await userEvent.click(await screen.findByRole('button', { name: /gmail/i }))

    const select = screen.getByLabelText('Google account')
    await userEvent.selectOptions(select, 'conn-g1')
    // Address auto-fills from the connection's account label.
    expect(screen.getByLabelText(/address/i)).toHaveValue('support@acme.com')
    // Provider hosts are shown read-only — the backend sets them.
    expect(screen.getByText(/smtp\.gmail\.com:587/i)).toBeInTheDocument()
    expect(screen.getByText(/imap\.gmail\.com:993/i)).toBeInTheDocument()
  })
})

describe('EmailInboxWizard — transport fields', () => {
  async function pick(label: RegExp) {
    renderApp(<EmailInboxWizard open onOpenChange={() => {}} defaultName="Support email" />)
    await userEvent.click(await screen.findByRole('button', { name: label }))
  }

  it('SMTP renders host/port/username/security and blocks save until required fields + password', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/w1/integrations': () => ({ body: emptyIntegrations }) })
    await pick(/smtp \/ imap/i)

    expect(screen.getByLabelText(/smtp host/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Port')).toHaveValue(587)
    expect(screen.getByLabelText('Security')).toHaveValue('starttls')
    const password = screen.getByLabelText(/smtp password/i)
    expect(password).toHaveAttribute('type', 'password')

    const save = screen.getByRole('button', { name: /create inbox/i })
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/address/i), 'support@acme.com')
    await userEvent.type(screen.getByLabelText(/smtp host/i), 'mail.acme.com')
    await userEvent.type(screen.getByLabelText(/username/i), 'support@acme.com')
    expect(save).toBeDisabled()
    await userEvent.type(password, 'hunter2')
    expect(save).toBeEnabled()
  })

  it('SES renders region + key secrets; Mailgun renders domain + region + keys', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/w1/integrations': () => ({ body: emptyIntegrations }) })
    await pick(/amazon ses/i)

    expect(screen.getByLabelText(/aws region/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/access key id/i)).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText(/secret access key/i)).toHaveAttribute('type', 'password')

    // Back to the picker, choose Mailgun instead.
    await userEvent.click(screen.getByRole('button', { name: 'Back' }))
    await userEvent.click(screen.getByRole('button', { name: /mailgun/i }))
    expect(screen.getByLabelText(/sending domain/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Region')).toHaveValue('us')
    expect(screen.getByLabelText(/api key/i)).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText(/webhook signing key/i)).toBeInTheDocument()
  })

  it('Forwarding only needs nothing beyond the address', async () => {
    setupAuth()
    mockFetch({ 'GET /api/v1/w/w1/integrations': () => ({ body: emptyIntegrations }) })
    await pick(/forwarding only/i)

    const save = screen.getByRole('button', { name: /create inbox/i })
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/address/i), 'support@acme.com')
    expect(save).toBeEnabled()
  })
})

describe('EmailInboxWizard — create flow + finish screen', () => {
  it('creates a mailgun inbox and surfaces forward-to + the per-ESP webhook URL', async () => {
    setupAuth()
    let createBody: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({ body: emptyIntegrations }),
      'POST /api/v1/w/w1/inboxes': (init) => {
        createBody = JSON.parse(init!.body as string)
        return {
          status: 201,
          body: createdInbox(createBody!.config as Record<string, unknown>),
        }
      },
    })

    renderApp(<EmailInboxWizard open onOpenChange={() => {}} defaultName="Support email" />)
    await userEvent.click(await screen.findByRole('button', { name: /mailgun/i }))
    await userEvent.type(screen.getByLabelText(/address/i), 'support@acme.com')
    await userEvent.type(screen.getByLabelText(/sending domain/i), 'mg.acme.com')
    await userEvent.selectOptions(screen.getByLabelText('Region'), 'eu')
    await userEvent.type(screen.getByLabelText(/^api key/i), 'key-123')
    await userEvent.click(screen.getByRole('button', { name: /create inbox/i }))

    // Finish screen: copyable forward-to + provider-specific inbound URL.
    expect(await screen.findByText('in-abc123def456@in.stept.dev')).toBeInTheDocument()
    expect(
      screen.getByText('http://localhost:8600/api/channels/email/inbound/mailgun/nb1/tok123')
    ).toBeInTheDocument()
    expect(screen.getByText(/receiving route/i)).toBeInTheDocument()

    expect(createBody!).toMatchObject({
      name: 'Support email',
      channel_type: 'email',
      enabled: true,
      config: {
        address: 'support@acme.com',
        transport: 'mailgun',
        mailgun: { domain: 'mg.acme.com', base: 'eu' },
      },
      secrets: { mailgun_api_key: 'key-123' },
    })
  })

  it('generic transports get the tokenized generic inbound URL and IMAP saves on Done', async () => {
    setupAuth()
    let patchBody: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({ body: emptyIntegrations }),
      'POST /api/v1/w/w1/inboxes': (init) => ({
        status: 201,
        body: createdInbox(
          (JSON.parse(init!.body as string) as { config: Record<string, unknown> }).config
        ),
      }),
      'PATCH /api/v1/w/w1/inboxes/nb1': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: createdInbox(patchBody!.config as Record<string, unknown>) }
      },
    })

    renderApp(<EmailInboxWizard open onOpenChange={() => {}} defaultName="Support email" />)
    await userEvent.click(await screen.findByRole('button', { name: /smtp \/ imap/i }))
    await userEvent.type(screen.getByLabelText(/address/i), 'support@acme.com')
    await userEvent.type(screen.getByLabelText(/smtp host/i), 'mail.acme.com')
    await userEvent.type(screen.getByLabelText(/username/i), 'support')
    await userEvent.type(screen.getByLabelText(/smtp password/i), 'hunter2')
    await userEvent.click(screen.getByRole('button', { name: /create inbox/i }))

    // Generic (non-ESP) inbound URL: no provider segment.
    expect(
      await screen.findByText('http://localhost:8600/api/channels/email/inbound/nb1/tok123')
    ).toBeInTheDocument()

    // Turn on IMAP polling, fill the mailbox, Done PATCHes it.
    await userEvent.click(screen.getByLabelText('IMAP polling'))
    await userEvent.type(screen.getByLabelText(/imap host/i), 'imap.acme.com')
    await userEvent.type(screen.getByLabelText(/imap username/i), 'support')
    await userEvent.type(screen.getByLabelText(/imap password/i), 'hunter2')
    const poll = screen.getByLabelText(/poll every/i)
    await userEvent.clear(poll)
    await userEvent.type(poll, '5')
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))

    await waitFor(() => expect(patchBody).not.toBeNull())
    expect(patchBody!.config).toMatchObject({
      imap: {
        enabled: true,
        host: 'imap.acme.com',
        port: 993,
        username: 'support',
        poll_minutes: 5,
      },
    })
    expect(patchBody!.secrets).toEqual({ imap_password: 'hunter2' })
  })
})

describe('EmailInboxWizard — edit mode', () => {
  it('seeds from the inbox config and PATCHes instead of creating', async () => {
    setupAuth()
    const inbox = createdInbox({
      address: 'help@acme.com',
      transport: 'postmark',
    })
    let patchBody: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({ body: emptyIntegrations }),
      'PATCH /api/v1/w/w1/inboxes/nb1': (init) => {
        patchBody = JSON.parse(init!.body as string)
        return { body: createdInbox(patchBody!.config as Record<string, unknown>) }
      },
    })

    renderApp(<EmailInboxWizard open onOpenChange={() => {}} inbox={inbox} />)

    // The stored transport arrives preselected on the picker.
    expect(await screen.findByRole('button', { name: /postmark/i })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

    expect(screen.getByLabelText(/address/i)).toHaveValue('help@acme.com')
    // Stored secrets: blank password field with "unchanged", save not blocked.
    expect(screen.getByLabelText(/server token/i)).toHaveAttribute('placeholder', 'unchanged')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(patchBody).not.toBeNull())
    expect(patchBody!.config).toMatchObject({
      address: 'help@acme.com',
      transport: 'postmark',
    })
    expect(patchBody!).not.toHaveProperty('secrets')
  })
})
