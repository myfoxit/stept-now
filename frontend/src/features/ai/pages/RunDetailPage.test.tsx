import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router'

import { mockFetch } from '@/test/helpers'

import { Component as RunDetailPage } from './RunDetailPage'
import { makeStep, renderPage, seedAuth } from '../test-utils'

const NOW = '2026-07-31T10:00:00Z'

describe('RunDetailPage', () => {
  beforeEach(() => seedAuth())

  it('renders the run summary and full step trace', async () => {
    mockFetch({
      'GET /api/v1/w/w1/ai/runs/r1': () => ({
        body: {
          run: {
            id: 'r1',
            conversation_id: 'c1',
            agent_id: 'a1',
            agent_name: 'Sage',
            trigger_message_id: null,
            status: 'completed',
            error: null,
            input_tokens: 120,
            output_tokens: 45,
            started_at: NOW,
            finished_at: NOW,
            reply_message_id: 'm1',
            created_at: NOW,
          },
          steps: [
            makeStep({ id: 's1', ord: 0, kind: 'llm_call' }),
            makeStep({ id: 's2', ord: 1, kind: 'tool_call', name: 'search_knowledge', input: { query: 'widget' } }),
            makeStep({ id: 's3', ord: 2, kind: 'tool_result', name: 'search_knowledge', output: { results: [] } }),
            makeStep({ id: 's4', ord: 3, kind: 'final_reply', output: { content: 'Here you go [1].' } }),
          ],
        },
      }),
    })
    renderPage(
      <Routes>
        <Route path="/ai/runs/:runId" element={<RunDetailPage />} />
      </Routes>,
      { route: '/ai/runs/r1' }
    )

    expect(await screen.findByText('Sage run')).toBeInTheDocument()
    expect(screen.getByText('LLM call')).toBeInTheDocument()
    expect(screen.getByText('Tool call')).toBeInTheDocument()
    expect(screen.getByText('Tool result')).toBeInTheDocument()
    expect(screen.getByText('Final reply')).toBeInTheDocument()
    expect(screen.getByText('Here you go [1].')).toBeInTheDocument()
    expect(screen.getByText('120')).toBeInTheDocument()
    expect(screen.getByText('Trace (4 steps)')).toBeInTheDocument()
  })
})
