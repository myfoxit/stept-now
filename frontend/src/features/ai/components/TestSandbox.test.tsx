import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { TestSandbox } from './TestSandbox'
import { makeStep, seedAuth } from '../test-utils'

describe('TestSandbox', () => {
  beforeEach(() => seedAuth())

  it('renders the step trace, reply and citations from a test run', async () => {
    mockFetch({
      'POST /api/v1/w/w1/ai/agents/a1/test': () => ({
        body: {
          reply: 'Paste the loader snippet before the closing body tag [1].',
          status: 'completed',
          steps: [
            makeStep({ id: 's1', ord: 0, kind: 'llm_call' }),
            makeStep({ id: 's2', ord: 1, kind: 'tool_call', name: 'search_knowledge', input: { query: 'widget' } }),
            makeStep({
              id: 's3',
              ord: 2,
              kind: 'final_reply',
              output: { content: 'Paste the loader snippet before the closing body tag [1].' },
            }),
          ],
          citations: [{ n: 1, title: 'Installing the widget', url: null, document_id: 'd1' }],
        },
      }),
    })
    renderApp(<TestSandbox agentId="a1" />)

    await userEvent.type(screen.getByLabelText(/sandbox message/i), 'how do I install the widget')
    await userEvent.click(screen.getByRole('button', { name: /run test/i }))

    expect((await screen.findAllByText(/paste the loader snippet/i)).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('LLM call')).toBeInTheDocument()
    expect(screen.getByText('Tool call')).toBeInTheDocument()
    expect(screen.getByText('search_knowledge')).toBeInTheDocument()
    expect(screen.getByText('Final reply')).toBeInTheDocument()
    expect(screen.getByText('Installing the widget')).toBeInTheDocument()
  })
})
