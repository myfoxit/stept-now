import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch } from '@/test/helpers'

import { Component as SearchPlaygroundPage } from './SearchPlaygroundPage'
import { renderPage, seedAuth } from '../test-utils'

describe('SearchPlaygroundPage', () => {
  beforeEach(() => seedAuth())

  it('runs a search and renders ranked results with scores', async () => {
    mockFetch({
      'GET /api/v1/w/w1/knowledge/sources': () => ({ body: [] }),
      'POST /api/v1/w/w1/knowledge/search': () => ({
        body: {
          results: [
            {
              chunk_id: 'c1',
              document_id: 'd1',
              content: 'Install the widget by pasting the loader snippet before </body>.',
              score: 0.42,
              title: 'Installing the widget',
              url: null,
              ord: 0,
            },
          ],
          latency_ms: 12,
        },
      }),
    })
    renderPage(<SearchPlaygroundPage />)

    await userEvent.type(
      screen.getByLabelText(/search query/i),
      'How do I install the widget?'
    )
    await userEvent.click(screen.getByRole('button', { name: /^search$/i }))

    expect(await screen.findByText('Installing the widget')).toBeInTheDocument()
    expect(screen.getByText(/pasting the loader snippet/i)).toBeInTheDocument()
    // Shown relative to the best hit; the raw fused score lives in the tooltip
    // because an RRF value like 0.0164 reads as "irrelevant" to a human.
    const score = screen.getByText('100% of top hit')
    expect(score).toBeInTheDocument()
    expect(score).toHaveAttribute('title', 'Fused score 0.4200')
    await waitFor(() => expect(screen.getByText(/1 result in 12 ms/i)).toBeInTheDocument())
  })

  it('sends rerank: true when the "Rerank with AI" switch is on', async () => {
    const fetchFn = mockFetch({
      'GET /api/v1/w/w1/knowledge/sources': () => ({ body: [] }),
      'POST /api/v1/w/w1/knowledge/search': () => ({ body: { results: [], latency_ms: 5 } }),
    })
    renderPage(<SearchPlaygroundPage />)

    await userEvent.click(screen.getByRole('switch', { name: /rerank with ai/i }))
    await userEvent.type(screen.getByLabelText(/search query/i), 'refund policy')
    await userEvent.click(screen.getByRole('button', { name: /^search$/i }))

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) => init?.method === 'POST' && String(url).endsWith('/knowledge/search')
      )
      expect(call).toBeDefined()
      expect(JSON.parse(String(call![1]?.body))).toMatchObject({
        query: 'refund policy',
        rerank: true,
      })
    })
  })

  it('omits rerank from the body when the switch is off', async () => {
    const fetchFn = mockFetch({
      'GET /api/v1/w/w1/knowledge/sources': () => ({ body: [] }),
      'POST /api/v1/w/w1/knowledge/search': () => ({ body: { results: [], latency_ms: 5 } }),
    })
    renderPage(<SearchPlaygroundPage />)

    await userEvent.type(screen.getByLabelText(/search query/i), 'refund policy')
    await userEvent.click(screen.getByRole('button', { name: /^search$/i }))

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) => init?.method === 'POST' && String(url).endsWith('/knowledge/search')
      )
      expect(call).toBeDefined()
      expect(JSON.parse(String(call![1]?.body))).not.toHaveProperty('rerank')
    })
  })
})
