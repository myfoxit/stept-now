import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch, renderApp } from '@/test/helpers'

import { ArticleEditor } from './ArticleEditor'
import { seedAuth } from '../test-utils'

const NOW = '2026-07-31T10:00:00Z'

function makeArticle(overrides: Record<string, unknown> = {}) {
  return {
    id: 'a1',
    title: 'Refund policy',
    slug: 'refund-policy',
    body: '# Refunds\n\nWe refund within **30 days**.',
    status: 'draft',
    collection_id: null,
    author_id: null,
    published_at: null,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}

function mockArticleRoutes(overrides: Record<string, unknown> = {}) {
  return mockFetch({
    'GET /api/v1/w/w1/articles/a1': () => ({ body: makeArticle(overrides) }),
    'PATCH /api/v1/w/w1/articles/a1': () => ({ body: makeArticle(overrides) }),
  })
}

const render = () =>
  renderApp(<ArticleEditor articleId="a1" collections={[]} onDeleted={() => {}} />)

describe('ArticleEditor', () => {
  beforeEach(() => seedAuth())

  it('loads the article body into the rich text editor', async () => {
    mockArticleRoutes()
    render()

    const editor = await screen.findByRole('textbox', { name: /article body/i })
    await waitFor(() => expect(editor.querySelector('h1')?.textContent).toBe('Refunds'))
    expect(editor.querySelector('strong')?.textContent).toBe('30 days')
  })

  it('keeps the markdown tab in sync with edits made in the rich editor', async () => {
    mockArticleRoutes()
    render()

    const editor = await screen.findByRole('textbox', { name: /article body/i })
    await userEvent.click(editor)
    await userEvent.keyboard('{Control>}a{/Control}')
    await userEvent.keyboard('Rewritten in the editor')

    await userEvent.click(screen.getByRole('tab', { name: /markdown/i }))
    const raw = await screen.findByRole('textbox', { name: /article markdown/i })
    expect(raw).toHaveValue('Rewritten in the editor')
  })

  it('feeds markdown-tab edits back into the rich editor without losing content', async () => {
    mockArticleRoutes()
    render()

    await screen.findByRole('textbox', { name: /article body/i })
    await userEvent.click(screen.getByRole('tab', { name: /markdown/i }))

    const raw = await screen.findByRole('textbox', { name: /article markdown/i })
    await userEvent.clear(raw)
    await userEvent.type(raw, '## Rewritten\n\n- one\n- two')

    await userEvent.click(screen.getByRole('tab', { name: /write/i }))
    const editor = await screen.findByRole('textbox', { name: /article body/i })
    await waitFor(() => expect(editor.querySelector('h2')?.textContent).toBe('Rewritten'))
    expect(editor.querySelectorAll('ul li')).toHaveLength(2)
  })

  it('previews the current markdown through the shared renderer', async () => {
    mockArticleRoutes()
    render()

    await screen.findByRole('textbox', { name: /article body/i })
    await userEvent.click(screen.getByRole('tab', { name: /preview/i }))
    expect(await screen.findByText('Refunds')).toBeInTheDocument()
    expect(screen.getByText('30 days').tagName).toBe('STRONG')
  })

  it('saves the markdown body, not HTML', async () => {
    const fetchFn = mockArticleRoutes()
    render()

    const editor = await screen.findByRole('textbox', { name: /article body/i })
    await userEvent.click(editor)
    await userEvent.keyboard('{Control>}a{/Control}')
    await userEvent.keyboard('Plain rewrite')
    await userEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(([, init]) => init?.method === 'PATCH')
      expect(call).toBeDefined()
      const body = JSON.parse(String(call![1]?.body))
      expect(body.body).toBe('Plain rewrite')
      expect(body.body).not.toContain('<')
    })
  })

  it('hides write actions and locks the editor without knowledge:write', async () => {
    seedAuth(['knowledge:read'])
    mockArticleRoutes()
    render()

    await screen.findByRole('textbox', { name: /article body/i })
    expect(screen.queryByRole('button', { name: /save/i })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: /markdown/i }))
    expect(await screen.findByRole('textbox', { name: /article markdown/i })).toBeDisabled()
  })
})
