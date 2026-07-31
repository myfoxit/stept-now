import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router'

import { mockFetch } from '@/test/helpers'

import { Component as SourceDetailPage } from './SourceDetailPage'
import { makeDocument, makeSource, renderPage, seedAuth } from '../test-utils'

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
})
