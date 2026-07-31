import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { AddProviderDialog } from './AddProviderDialog'
import { seedAuth } from '../test-utils'

const NOW = '2026-07-31T10:00:00Z'

describe('AddProviderDialog', () => {
  beforeEach(() => seedAuth())

  it('adds a provider with the chosen kind and a write-only key', async () => {
    const fetchFn = mockFetch({
      'POST /api/v1/w/w1/ai/providers': () => ({
        status: 201,
        body: {
          id: 'p1',
          kind: 'anthropic',
          name: 'Anthropic',
          base_url: null,
          enabled: true,
          meta: {},
          has_key: true,
          api_key_hint: '…abc4',
          created_at: NOW,
          updated_at: NOW,
        },
      }),
    })
    renderApp(<AddProviderDialog open onOpenChange={() => {}} />)

    await userEvent.selectOptions(screen.getByRole('combobox', { name: /provider/i }), 'anthropic')
    await userEvent.type(screen.getByLabelText(/display name/i), 'Anthropic')
    await userEvent.type(screen.getByLabelText(/api key/i), 'sk-secret-123')
    await userEvent.click(screen.getByRole('button', { name: /add provider/i }))

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) => init?.method === 'POST' && String(url).endsWith('/ai/providers')
      )
      expect(call).toBeDefined()
      const body = JSON.parse(String(call![1]?.body))
      expect(body).toMatchObject({ kind: 'anthropic', name: 'Anthropic', api_key: 'sk-secret-123' })
    })
  })
})
