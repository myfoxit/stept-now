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

  it('shows auto-sync and credentials badges on a connector source card', async () => {
    mockFetch({
      'GET /api/v1/w/w1/knowledge/sources': () => ({
        body: [
          makeSource({
            id: 'gh1',
            type: 'github',
            name: 'Docs repo',
            config: { repo_owner: 'acme', repo: 'docs', refresh_minutes: 30 },
            has_secrets: true,
            last_synced_at: '2026-07-31T09:00:00Z',
          }),
        ],
      }),
    })
    renderPage(<KnowledgePage />)

    expect(await screen.findByText('Docs repo')).toBeInTheDocument()
    expect(screen.getByText(/Auto-sync: every 30m/)).toBeInTheDocument()
    expect(screen.getByText(/Credentials set/)).toBeInTheDocument()
    expect(screen.getByText(/GitHub/)).toBeInTheDocument()
    // github is a remote type, so the re-sync affordance is available
    expect(screen.getByRole('button', { name: /re-sync source/i })).toBeInTheDocument()
  })

  it('does not show connector badges on a plain files source', async () => {
    mockFetch({
      'GET /api/v1/w/w1/knowledge/sources': () => ({ body: [makeSource({ type: 'files' })] }),
    })
    renderPage(<KnowledgePage />)

    await screen.findByText('Product docs')
    expect(screen.queryByText(/Auto-sync/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Credentials set/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /re-sync source/i })).not.toBeInTheDocument()
  })
})
