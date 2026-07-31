import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch } from '@/test/helpers'

import { Component as KnowledgePage } from './KnowledgePage'
import { makeSource, renderPage, seedAuth } from '../test-utils'

describe('KnowledgePage', () => {
  beforeEach(() => seedAuth())

  it('renders source cards with document counts and status', async () => {
    mockFetch({
      'GET /api/v1/w/w1/knowledge/sources': () => ({
        body: [makeSource({ name: 'Product docs', document_count: 3, status: 'idle' })],
      }),
    })
    renderPage(<KnowledgePage />)
    expect(await screen.findByText('Product docs')).toBeInTheDocument()
    expect(screen.getByText(/3 documents/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /add source/i })).toBeInTheDocument()
  })

  it('shows an empty state when there are no sources', async () => {
    mockFetch({ 'GET /api/v1/w/w1/knowledge/sources': () => ({ body: [] }) })
    renderPage(<KnowledgePage />)
    expect(await screen.findByText(/no knowledge sources yet/i)).toBeInTheDocument()
  })

  it('hides create controls without knowledge:write', async () => {
    seedAuth(['knowledge:read'])
    mockFetch({ 'GET /api/v1/w/w1/knowledge/sources': () => ({ body: [makeSource()] }) })
    renderPage(<KnowledgePage />)
    await screen.findByText('Product docs')
    expect(screen.queryByRole('button', { name: /add source/i })).not.toBeInTheDocument()
  })
})
