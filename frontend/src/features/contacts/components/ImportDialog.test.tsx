import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { useAuthStore } from '@/stores/auth'
import { mockFetch, renderApp } from '@/test/helpers'
import { ImportDialog } from '@/features/contacts/components/ImportDialog'

const ISO = '2026-01-01T00:00:00Z'

function setupAuth() {
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
        permissions: ['contacts:read', 'contacts:write'],
        workspace: { id: 'ws1', name: 'WS', slug: 'ws', settings: {} },
      },
    ],
  })
}

function preview(mapping: Record<string, string>) {
  return {
    contact_import: {
      id: 'imp1',
      filename: 'contacts.csv',
      status: 'pending',
      mapping,
      total_rows: 2,
      processed_rows: 0,
      created_count: 0,
      updated_count: 0,
      failed_count: 0,
      errors: [],
      started_at: null,
      completed_at: null,
      created_at: ISO,
    },
    headers: Object.keys(mapping),
    sample_rows: [{ Email: 'ada@example.com', Plan: 'pro' }],
  }
}

function csv() {
  return new File(['Email,Plan\nada@example.com,pro\n'], 'contacts.csv', { type: 'text/csv' })
}

afterEach(() => {
  cleanup()
  useAuthStore.setState({ accessToken: null, memberships: [], workspaceId: null, user: null })
})

describe('ImportDialog', () => {
  it('shows the detected columns and the suggested mapping after upload', async () => {
    setupAuth()
    mockFetch({
      'POST /api/v1/w/ws1/contacts/imports': () => ({
        body: preview({ Email: 'email', Plan: '' }),
        status: 201,
      }),
    })
    renderApp(<ImportDialog open onOpenChange={() => {}} />)

    await userEvent.upload(screen.getByLabelText('CSV file'), csv())
    expect(await screen.findByText(/2 rows/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Map column Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Map column Plan')).toBeInTheDocument()
  })

  it('starts the run with the resolved mapping, dropping skipped columns', async () => {
    setupAuth()
    let body: { mapping?: Record<string, string> } | null = null
    mockFetch({
      'POST /api/v1/w/ws1/contacts/imports': () => ({
        body: preview({ Email: 'email', Plan: '' }),
        status: 201,
      }),
      'POST /api/v1/w/ws1/contacts/imports/imp1/start': (init) => {
        body = JSON.parse(String(init?.body))
        return { body: preview({ Email: 'email' }).contact_import }
      },
    })
    renderApp(<ImportDialog open onOpenChange={() => {}} />)

    await userEvent.upload(screen.getByLabelText('CSV file'), csv())
    await screen.findByText(/2 rows/i)
    await userEvent.click(screen.getByRole('button', { name: /start import/i }))

    await waitFor(() => expect(body).not.toBeNull())
    expect(body!.mapping).toEqual({ Email: 'email' })
  })

  it('refuses to start without an identifying column', async () => {
    setupAuth()
    mockFetch({
      'POST /api/v1/w/ws1/contacts/imports': () => ({
        body: preview({ Email: '', Plan: '' }),
        status: 201,
      }),
    })
    renderApp(<ImportDialog open onOpenChange={() => {}} />)

    await userEvent.upload(screen.getByLabelText('CSV file'), csv())
    expect(await screen.findByText(/map at least one of email/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start import/i })).toBeDisabled()
  })

  it('maps a column onto a custom attribute key', async () => {
    setupAuth()
    let body: { mapping?: Record<string, string> } | null = null
    mockFetch({
      'POST /api/v1/w/ws1/contacts/imports': () => ({
        body: preview({ Email: 'email', Plan: '' }),
        status: 201,
      }),
      'POST /api/v1/w/ws1/contacts/imports/imp1/start': (init) => {
        body = JSON.parse(String(init?.body))
        return { body: preview({ Email: 'email' }).contact_import }
      },
    })
    renderApp(<ImportDialog open onOpenChange={() => {}} />)

    await userEvent.upload(screen.getByLabelText('CSV file'), csv())
    await screen.findByText(/2 rows/i)
    await userEvent.click(screen.getByLabelText('Map column Plan'))
    await userEvent.click(await screen.findByRole('option', { name: /custom attribute/i }))
    await userEvent.type(screen.getByLabelText('Attribute key for Plan'), 'plan')
    await userEvent.click(screen.getByRole('button', { name: /start import/i }))

    await waitFor(() => expect(body).not.toBeNull())
    expect(body!.mapping).toEqual({ Email: 'email', Plan: 'attributes.plan' })
  })
})
