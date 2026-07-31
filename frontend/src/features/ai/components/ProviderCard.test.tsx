import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { ProviderCard } from './ProviderCard'
import type { Provider } from '../api'
import { seedAuth } from '../test-utils'

const NOW = '2026-07-31T10:00:00Z'

const PROVIDER: Provider = {
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
}

describe('ProviderCard', () => {
  beforeEach(() => seedAuth())

  it('enables a catalog model by creating it under the provider', async () => {
    const fetchFn = mockFetch({
      'GET /api/v1/w/w1/ai/catalog': () => ({
        body: {
          anthropic: [
            {
              model_key: 'claude-opus-5',
              display_name: 'Claude Opus 5',
              modality: 'chat',
              context_window: 1000000,
            },
          ],
        },
      }),
      'GET /api/v1/w/w1/ai/providers/p1/models': () => ({ body: [] }),
      'POST /api/v1/w/w1/ai/providers/p1/models': () => ({
        status: 201,
        body: {
          id: 'm1',
          provider_id: 'p1',
          model_key: 'claude-opus-5',
          display_name: 'Claude Opus 5',
          modality: 'chat',
          context_window: 1000000,
          enabled: true,
          is_default: false,
          created_at: NOW,
        },
      }),
    })
    renderApp(<ProviderCard provider={PROVIDER} />)

    const enableButton = await screen.findByRole('button', { name: /claude opus 5/i })
    await userEvent.click(enableButton)

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) => init?.method === 'POST' && String(url).endsWith('/providers/p1/models')
      )
      expect(call).toBeDefined()
      expect(JSON.parse(String(call![1]?.body))).toMatchObject({ model_key: 'claude-opus-5' })
    })
  })

  it('shows the masked key hint', async () => {
    mockFetch({
      'GET /api/v1/w/w1/ai/catalog': () => ({ body: { anthropic: [] } }),
      'GET /api/v1/w/w1/ai/providers/p1/models': () => ({ body: [] }),
    })
    renderApp(<ProviderCard provider={PROVIDER} />)
    expect(await screen.findByText(/Key …abc4/)).toBeInTheDocument()
  })
})
