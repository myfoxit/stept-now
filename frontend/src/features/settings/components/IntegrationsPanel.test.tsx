import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'

import type { IntegrationConnection, IntegrationProvider } from '../api'
import { IntegrationsPanel } from './IntegrationsPanel'
import { assignLocation } from './integrations/redirect'

// jsdom's window.location is unforgeable, so OAuth handoffs go through this seam.
vi.mock('./integrations/redirect', () => ({ assignLocation: vi.fn() }))

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
        permissions: ['integrations:manage'],
        workspace: { id: 'w1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

function connection(overrides: Partial<IntegrationConnection> = {}): IntegrationConnection {
  return {
    id: 'conn1',
    provider: 'google',
    status: 'connected',
    account_label: 'support@acme.com',
    scopes: [],
    meta: {},
    created_at: ISO,
    ...overrides,
  }
}

function provider(overrides: Partial<IntegrationProvider> = {}): IntegrationProvider {
  return {
    id: 'google',
    name: 'Google (Gmail & Drive)',
    category: 'email',
    auth: 'oauth2',
    description: 'Gmail sending and Drive knowledge sync.',
    doc_slug: 'google',
    configured: true,
    connections: [],
    credential: {
      client_id: null,
      has_secret: false,
      fields: ['client_id', 'client_secret'],
      redirect_uri: 'http://localhost:8600/api/integrations/oauth/google/callback',
      from_env: false,
    },
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('IntegrationsPanel', () => {
  it('renders category groups with connected / needs-setup / not-connected chips', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({
        body: {
          providers: [
            provider({ connections: [connection()] }),
            provider({
              id: 'microsoft',
              name: 'Microsoft 365',
              configured: false,
              credential: {
                client_id: null,
                has_secret: false,
                fields: ['client_id', 'client_secret'],
                redirect_uri: 'http://localhost:8600/api/integrations/oauth/microsoft/callback',
                from_env: false,
              },
            }),
            provider({
              id: 'slack',
              name: 'Slack',
              category: 'channel',
              connections: [],
            }),
          ],
        },
      }),
    })

    renderApp(<IntegrationsPanel />)

    expect(await screen.findByText('Google (Gmail & Drive)')).toBeInTheDocument()
    expect(screen.getByText('Connected (1)')).toBeInTheDocument()
    expect(screen.getByText('Needs setup')).toBeInTheDocument()
    expect(screen.getByText('Not connected')).toBeInTheDocument()
    // Category sections in fixed order; empty categories are dropped.
    expect(screen.getByRole('heading', { name: 'Email' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Channels' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Knowledge' })).not.toBeInTheDocument()
  })

  it('flags reauth_required connections and never counts revoked ones', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({
        body: {
          providers: [
            provider({
              connections: [
                connection({ id: 'c1', status: 'reauth_required' }),
                connection({ id: 'c2', status: 'revoked', account_label: 'old@acme.com' }),
              ],
            }),
          ],
        },
      }),
    })

    renderApp(<IntegrationsPanel />)

    expect(await screen.findByText('Connected (1)')).toBeInTheDocument()
    expect(screen.getByText('Reauthorize')).toBeInTheDocument()
    expect(screen.queryByText('old@acme.com')).not.toBeInTheDocument()
  })

  it('Connect POSTs to the connect endpoint and hands the browser to the authorize URL', async () => {
    setupAuth()
    let connectBody: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({ body: { providers: [provider()] } }),
      'POST /api/v1/w/w1/integrations/google/connect': (init) => {
        connectBody = JSON.parse(init!.body as string)
        return { body: { authorize_url: 'https://accounts.google.com/o/oauth2/v2/auth?x=1' } }
      },
    })

    renderApp(<IntegrationsPanel />)
    await userEvent.click(await screen.findByRole('button', { name: 'Connect' }))

    await waitFor(() =>
      expect(vi.mocked(assignLocation)).toHaveBeenCalledWith(
        'https://accounts.google.com/o/oauth2/v2/auth?x=1'
      )
    )
    expect(connectBody).toEqual({ return_to: '/settings/integrations' })
  })

  it('disables Connect while unconfigured and Reconnect hits the reconnect endpoint', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({
        body: {
          providers: [
            provider({ configured: false }),
            provider({
              id: 'confluence',
              name: 'Confluence',
              category: 'knowledge',
              connections: [connection({ id: 'c9', provider: 'confluence' })],
            }),
          ],
        },
      }),
      'POST /api/v1/w/w1/integrations/connections/c9/reconnect': () => ({
        body: { authorize_url: 'https://auth.atlassian.com/authorize?again=1' },
      }),
    })

    renderApp(<IntegrationsPanel />)

    const buttons = await screen.findAllByRole('button', { name: 'Connect' })
    expect(buttons[0]).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: /reconnect support@acme.com/i }))
    await waitFor(() =>
      expect(vi.mocked(assignLocation)).toHaveBeenCalledWith(
        'https://auth.atlassian.com/authorize?again=1'
      )
    )
  })

  it('disconnect asks for confirmation before DELETEing the connection', async () => {
    setupAuth()
    const fetchFn = mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({
        body: { providers: [provider({ connections: [connection()] })] },
      }),
      'DELETE /api/v1/w/w1/integrations/connections/conn1': () => ({
        body: { message: 'ok' },
      }),
    })

    renderApp(<IntegrationsPanel />)
    await userEvent.click(
      await screen.findByRole('button', { name: /disconnect support@acme.com/i })
    )
    // Nothing deleted yet — the confirm dialog gates it.
    expect(
      fetchFn.mock.calls.find(([, init]) => init?.method === 'DELETE')
    ).toBeUndefined()

    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' }))
    await waitFor(() =>
      expect(
        fetchFn.mock.calls.find(([, init]) => init?.method === 'DELETE')
      ).toBeDefined()
    )
  })

  it('credential form: write-only secret, copyable redirect URI, PUT payload', async () => {
    setupAuth()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    let putBody: Record<string, unknown> | null = null
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({
        body: {
          providers: [
            provider({
              id: 'slack',
              name: 'Slack',
              category: 'channel',
              credential: {
                client_id: '123.456',
                has_secret: true,
                fields: ['client_id', 'client_secret', 'signing_secret'],
                redirect_uri: 'http://localhost:8600/api/integrations/oauth/slack/callback',
                from_env: false,
              },
            }),
          ],
        },
      }),
      'PUT /api/v1/w/w1/integrations/slack/credentials': (init) => {
        putBody = JSON.parse(init!.body as string)
        return {
          body: {
            client_id: '123.456',
            has_secret: true,
            fields: ['client_id', 'client_secret', 'signing_secret'],
            redirect_uri: 'http://localhost:8600/api/integrations/oauth/slack/callback',
            from_env: false,
          },
        }
      },
    })

    renderApp(<IntegrationsPanel />)
    await userEvent.click(await screen.findByRole('button', { name: /use your own app/i }))

    // Secret is write-only: password field, blank, "unchanged" placeholder.
    const secret = screen.getByLabelText('Client secret')
    expect(secret).toHaveAttribute('type', 'password')
    expect(secret).toHaveValue('')
    expect(secret).toHaveAttribute('placeholder', 'unchanged')
    // Provider extra field from credential.fields renders too.
    expect(screen.getByLabelText('Signing secret')).toHaveAttribute('type', 'password')

    const redirect = screen.getByLabelText('Redirect URI')
    expect(redirect).toHaveAttribute('readonly')
    await userEvent.click(screen.getByRole('button', { name: /copy redirect uri/i }))
    expect(writeText).toHaveBeenCalledWith(
      'http://localhost:8600/api/integrations/oauth/slack/callback'
    )

    await userEvent.type(screen.getByLabelText('Signing secret'), 'shhh')
    await userEvent.click(screen.getByRole('button', { name: /save credentials/i }))
    await waitFor(() => expect(putBody).not.toBeNull())
    // Untyped client_secret is omitted (= keep stored); typed extras ship.
    expect(putBody!).toEqual({ client_id: '123.456', extra: { signing_secret: 'shhh' } })
  })

  it('toasts the ?connected= and ?error= params the OAuth callback lands with', async () => {
    setupAuth()
    const success = vi.spyOn(toast, 'success')
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({ body: { providers: [provider()] } }),
    })

    renderApp(<IntegrationsPanel />, { route: '/settings/integrations?connected=google' })
    await waitFor(() => expect(success).toHaveBeenCalledWith('Google connected'))

    cleanup()
    const error = vi.spyOn(toast, 'error')
    renderApp(<IntegrationsPanel />, { route: '/settings/integrations?error=access_denied' })
    await waitFor(() =>
      expect(error).toHaveBeenCalledWith('You cancelled the authorization at the provider.')
    )
  })

  it('Zendesk gets an explainer pointing at the knowledge source dialog, not a Connect button', async () => {
    setupAuth()
    mockFetch({
      'GET /api/v1/w/w1/integrations': () => ({
        body: {
          providers: [
            provider({
              id: 'zendesk',
              name: 'Zendesk',
              category: 'knowledge',
              auth: 'token',
              configured: false,
              credential: null,
            }),
          ],
        },
      }),
    })

    renderApp(<IntegrationsPanel />)
    expect(await screen.findByText('Zendesk')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Connect' })).not.toBeInTheDocument()
    const link = screen.getByRole('link', { name: /knowledge → add source → zendesk/i })
    expect(link).toHaveAttribute('href', '/knowledge')
  })
})
