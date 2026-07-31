import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router'

import { mockFetch } from '@/test/helpers'

import { Component as SourceDetailPage } from './SourceDetailPage'
import {
  makeDocument,
  makeDocumentDetail,
  makeSource,
  renderPage,
  seedAuth,
} from '../test-utils'

const renderPageAt = () =>
  renderPage(
    <Routes>
      <Route path="/knowledge/sources/:sourceId" element={<SourceDetailPage />} />
    </Routes>,
    { route: '/knowledge/sources/src1' }
  )

describe('SourceDetailPage', () => {
  beforeEach(() => seedAuth())

  it('renders the documents table with status badges', async () => {
    mockFetch({
      'GET /api/v1/w/w1/knowledge/sources/src1': () => ({
        body: makeSource({ id: 'src1', name: 'Product docs' }),
      }),
      'GET /api/v1/w/w1/knowledge/documents': () => ({
        body: {
          items: [
            makeDocument({ id: 'd1', title: 'Getting started', status: 'indexed', token_count: 1200 }),
            makeDocument({ id: 'd2', title: 'Broken page', status: 'failed', error: 'parse error' }),
          ],
          total: 2,
          limit: 200,
          offset: 0,
        },
      }),
    })
    renderPage(
      <Routes>
        <Route path="/knowledge/sources/:sourceId" element={<SourceDetailPage />} />
      </Routes>,
      { route: '/knowledge/sources/src1' }
    )

    expect(await screen.findByText('Getting started')).toBeInTheDocument()
    expect(screen.getByText('Broken page')).toBeInTheDocument()
    expect(screen.getByText('Indexed')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry document/i })).toBeInTheDocument()
  })

  it('offers Edit only for authored text documents', async () => {
    mockFetch({
      'GET /api/v1/w/w1/knowledge/sources/src1': () => ({
        body: makeSource({ id: 'src1', type: 'files' }),
      }),
      'GET /api/v1/w/w1/knowledge/documents': () => ({
        body: {
          items: [
            makeDocument({ id: 'd1', title: 'Handbook', mime: 'text/markdown' }),
            makeDocument({ id: 'd2', title: 'Report', mime: 'application/pdf' }),
            makeDocument({ id: 'd3', title: 'Crawled', uri: 'https://example.com/a' }),
          ],
          total: 3,
          limit: 200,
          offset: 0,
        },
      }),
    })
    renderPageAt()

    expect(await screen.findByRole('button', { name: 'Edit Handbook' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit Report' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit Crawled' })).not.toBeInTheDocument()
  })

  it('loads the stored content into the editor and PATCHes the change', async () => {
    const fetchFn = mockFetch({
      'GET /api/v1/w/w1/knowledge/sources/src1': () => ({
        body: makeSource({ id: 'src1', type: 'text' }),
      }),
      'GET /api/v1/w/w1/knowledge/documents': () => ({
        body: {
          items: [makeDocument({ id: 'd1', title: 'Handbook' })],
          total: 1,
          limit: 200,
          offset: 0,
        },
      }),
      'GET /api/v1/w/w1/knowledge/documents/d1': () => ({
        body: makeDocumentDetail({
          id: 'd1',
          title: 'Handbook',
          content: '# Handbook\n\nOriginal body.',
        }),
      }),
      'PATCH /api/v1/w/w1/knowledge/documents/d1': () => ({
        body: makeDocument({ id: 'd1', title: 'Handbook' }),
      }),
    })
    renderPageAt()

    await userEvent.click(await screen.findByRole('button', { name: 'Edit Handbook' }))

    const editor = await screen.findByRole('textbox', { name: /document content/i })
    await waitFor(() => expect(editor.querySelector('h1')?.textContent).toBe('Handbook'))

    await userEvent.click(editor)
    await userEvent.keyboard('{Control>}a{/Control}')
    await userEvent.keyboard('Rewritten body')
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(([, init]) => init?.method === 'PATCH')
      expect(call).toBeDefined()
      expect(JSON.parse(String(call![1]?.body))).toEqual({
        title: 'Handbook',
        content: 'Rewritten body',
      })
    })
  })

  it('refuses to edit a document the detail endpoint reports as non-editable', async () => {
    mockFetch({
      'GET /api/v1/w/w1/knowledge/sources/src1': () => ({
        body: makeSource({ id: 'src1', type: 'text' }),
      }),
      'GET /api/v1/w/w1/knowledge/documents': () => ({
        body: {
          items: [makeDocument({ id: 'd1', title: 'Handbook' })],
          total: 1,
          limit: 200,
          offset: 0,
        },
      }),
      'GET /api/v1/w/w1/knowledge/documents/d1': () => ({
        body: makeDocumentDetail({ id: 'd1', title: 'Handbook', content: null }),
      }),
    })
    renderPageAt()

    await userEvent.click(await screen.findByRole('button', { name: 'Edit Handbook' }))

    expect(await screen.findByText(/can only be changed at the source/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save changes/i })).not.toBeInTheDocument()
  })

  it('hides every document action without knowledge:write', async () => {
    seedAuth(['knowledge:read'])
    mockFetch({
      'GET /api/v1/w/w1/knowledge/sources/src1': () => ({
        body: makeSource({ id: 'src1', type: 'text' }),
      }),
      'GET /api/v1/w/w1/knowledge/documents': () => ({
        body: {
          items: [makeDocument({ id: 'd1', title: 'Handbook' })],
          total: 1,
          limit: 200,
          offset: 0,
        },
      }),
    })
    renderPageAt()

    expect(await screen.findByText('Handbook')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit Handbook' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /delete document/i })).not.toBeInTheDocument()
  })
})
