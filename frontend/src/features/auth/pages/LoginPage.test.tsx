import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { Component as LoginPage } from '@/features/auth/pages/LoginPage'
import { mockFetch, renderApp } from '@/test/helpers'
import { useAuthStore } from '@/stores/auth'

const noProviders = {
  'GET /api/v1/auth/oauth/providers': () => ({ body: { providers: [] } }),
}

describe('LoginPage', () => {
  it('validates input before submitting', async () => {
    mockFetch({ ...noProviders })
    renderApp(<LoginPage />)
    await userEvent.click(screen.getByRole('button', { name: /log in/i }))
    expect(await screen.findByText(/enter a valid email/i)).toBeInTheDocument()
  })

  it('logs in and stores the session', async () => {
    useAuthStore.setState({ accessToken: null, user: null, memberships: [], bootstrapped: false })
    mockFetch({
      ...noProviders,
      'POST /api/v1/auth/login': () => ({
        body: {
          access_token: 'token-1',
          expires_in: 900,
          user: { id: 'u1', email: 'a@b.co', name: 'Ada' },
        },
      }),
      'GET /api/v1/me': () => ({
        body: { user: { id: 'u1', email: 'a@b.co', name: 'Ada' }, memberships: [] },
      }),
    })
    renderApp(<LoginPage />)
    await userEvent.type(screen.getByLabelText(/email/i), 'a@b.co')
    await userEvent.type(screen.getByLabelText(/password/i), 'password-123')
    await userEvent.click(screen.getByRole('button', { name: /log in/i }))
    await waitFor(() => expect(useAuthStore.getState().accessToken).toBe('token-1'))
    expect(useAuthStore.getState().user?.name).toBe('Ada')
  })

  it('renders social buttons only for configured providers', async () => {
    mockFetch({
      'GET /api/v1/auth/oauth/providers': () => ({ body: { providers: ['google', 'github'] } }),
    })
    renderApp(<LoginPage />)
    const google = await screen.findByRole('link', { name: /continue with google/i })
    expect(google).toHaveAttribute('href', '/api/v1/auth/oauth/google/start?next=%2F')
    expect(screen.getByRole('link', { name: /continue with github/i })).toHaveAttribute(
      'href',
      '/api/v1/auth/oauth/github/start?next=%2F'
    )
    expect(screen.getByText(/^or$/i)).toBeInTheDocument()
  })

  it('renders no social section when no provider is configured', async () => {
    const fetchMock = mockFetch({ ...noProviders })
    renderApp(<LoginPage />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole('link', { name: /continue with/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/^or$/i)).not.toBeInTheDocument()
  })

  it('surfaces oauth callback error codes as a destructive alert', async () => {
    mockFetch({ ...noProviders })
    renderApp(<LoginPage />, { route: '/login?error=email_unverified' })
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/isn't verified with that provider/i)
  })

  it('shows the cancelled copy for oauth_denied and nothing for unknown codes', async () => {
    mockFetch({ ...noProviders })
    renderApp(<LoginPage />, { route: '/login?error=oauth_denied' })
    expect(await screen.findByRole('alert')).toHaveTextContent(/cancelled at the provider/i)
  })

  it('ignores unknown error codes', async () => {
    mockFetch({ ...noProviders })
    renderApp(<LoginPage />, { route: '/login?error=not_a_known_code' })
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})
