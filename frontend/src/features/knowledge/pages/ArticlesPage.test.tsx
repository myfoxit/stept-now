import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch } from '@/test/helpers'

import { Component as ArticlesPage } from './ArticlesPage'
import { renderPage, seedAuth } from '../test-utils'

const NOW = '2026-07-31T10:00:00Z'

function articleList() {
  return {
    items: [
      {
        id: 'a1',
        collection_id: null,
        title: 'Welcome to Stept',
        slug: 'welcome',
        status: 'published',
        author_id: 'u1',
        published_at: NOW,
        created_at: NOW,
        updated_at: NOW,
      },
    ],
    total: 1,
    limit: 200,
    offset: 0,
  }
}

describe('ArticlesPage', () => {
  beforeEach(() => seedAuth())

  it('lists articles and opens one in the editor', async () => {
    mockFetch({
      'GET /api/v1/w/w1/articles/collections': () => ({ body: [] }),
      'GET /api/v1/w/w1/articles': () => ({ body: articleList() }),
      'GET /api/v1/w/w1/articles/a1': () => ({
        body: {
          id: 'a1',
          collection_id: null,
          title: 'Welcome to Stept',
          slug: 'welcome',
          status: 'published',
          author_id: 'u1',
          published_at: NOW,
          created_at: NOW,
          updated_at: NOW,
          body: '# Hello\n\nWelcome aboard.',
          meta: {},
        },
      }),
    })
    renderPage(<ArticlesPage />)

    const item = await screen.findByRole('button', { name: /welcome to stept/i })
    await userEvent.click(item)

    const titleInput = await screen.findByLabelText(/title/i)
    expect(titleInput).toHaveValue('Welcome to Stept')
    expect(screen.getByRole('button', { name: 'Unpublish' })).toBeInTheDocument()
  })

  it('creates a draft article', async () => {
    const fetchFn = mockFetch({
      'GET /api/v1/w/w1/articles/collections': () => ({ body: [] }),
      'GET /api/v1/w/w1/articles': () => ({ body: { items: [], total: 0, limit: 200, offset: 0 } }),
      'POST /api/v1/w/w1/articles': () => ({
        status: 201,
        body: {
          id: 'a2',
          collection_id: null,
          title: 'Untitled article',
          slug: 'untitled',
          status: 'draft',
          author_id: 'u1',
          published_at: null,
          created_at: NOW,
          updated_at: NOW,
          body: '',
          meta: {},
        },
      }),
      'GET /api/v1/w/w1/articles/a2': () => ({
        body: {
          id: 'a2',
          collection_id: null,
          title: 'Untitled article',
          slug: 'untitled',
          status: 'draft',
          author_id: 'u1',
          published_at: null,
          created_at: NOW,
          updated_at: NOW,
          body: '',
          meta: {},
        },
      }),
    })
    renderPage(<ArticlesPage />)

    await userEvent.click(await screen.findByRole('button', { name: 'New' }))
    await waitFor(() =>
      expect(
        fetchFn.mock.calls.some(
          ([url, init]) => init?.method === 'POST' && String(url).endsWith('/articles')
        )
      ).toBe(true)
    )
  })
})
