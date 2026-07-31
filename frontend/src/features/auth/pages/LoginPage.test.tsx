import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { Component as LoginPage } from '@/features/auth/pages/LoginPage'
import { mockFetch, renderApp } from '@/test/helpers'
import { useAuthStore } from '@/stores/auth'

describe('LoginPage', () => {
  it('validates input before submitting', async () => {
    mockFetch({})
    renderApp(<LoginPage />)
    await userEvent.click(screen.getByRole('button', { name: /log in/i }))
    expect(await screen.findByText(/enter a valid email/i)).toBeInTheDocument()
  })

  it('logs in and stores the session', async () => {
    useAuthStore.setState({ accessToken: null, user: null, memberships: [], bootstrapped: false })
    mockFetch({
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
})
