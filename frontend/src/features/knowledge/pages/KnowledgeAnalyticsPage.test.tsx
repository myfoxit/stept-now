import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { mockFetch } from '@/test/helpers'

import { Component as KnowledgeAnalyticsPage } from './KnowledgeAnalyticsPage'
import { makeAnalyticsOverview, renderPage, seedAuth } from '../test-utils'

describe('KnowledgeAnalyticsPage', () => {
  beforeEach(() => seedAuth(['knowledge:read', 'reports:read']))

  it('renders KPI tiles and both tables from the overview', async () => {
    mockFetch({
      'GET /api/v1/w/w1/knowledge/analytics': () => ({ body: makeAnalyticsOverview() }),
    })
    renderPage(<KnowledgeAnalyticsPage />)

    // KPI tiles
    expect(await screen.findByText('Total queries')).toBeInTheDocument()
    expect(screen.getByText('1.3K')).toBeInTheDocument() // compacted total
    expect(screen.getByText('12%')).toBeInTheDocument() // zero-result rate
    expect(screen.getByText('0.62')).toBeInTheDocument() // avg top score
    expect(screen.getByText('75%')).toBeInTheDocument() // AI deflection rate
    expect(screen.getByText(/Negative rate 13%/)).toBeInTheDocument()

    // Top queries table
    expect(screen.getByText('Top queries')).toBeInTheDocument()
    expect(screen.getByText('install widget')).toBeInTheDocument()
    expect(screen.getByText('0.71')).toBeInTheDocument()

    // Content gaps table + explanatory caption
    expect(screen.getByText('Content gaps')).toBeInTheDocument()
    expect(screen.getByText('sso setup')).toBeInTheDocument()
    expect(
      screen.getByText(/couldn’t answer — add content for these/i)
    ).toBeInTheDocument()
  })

  it('refetches with the selected day range', async () => {
    const fetchFn = mockFetch({
      'GET /api/v1/w/w1/knowledge/analytics': () => ({ body: makeAnalyticsOverview() }),
    })
    renderPage(<KnowledgeAnalyticsPage />)
    await screen.findByText('Total queries')

    expect(
      fetchFn.mock.calls.some(([url]) => String(url).includes('days=7'))
    ).toBe(true)

    await userEvent.selectOptions(screen.getByLabelText(/date range/i), '30')

    await waitFor(() => {
      expect(
        fetchFn.mock.calls.some(([url]) => String(url).includes('days=30'))
      ).toBe(true)
    })
  })

  it('shows an empty state when there is no search activity', async () => {
    mockFetch({
      'GET /api/v1/w/w1/knowledge/analytics': () => ({
        body: makeAnalyticsOverview({
          queries: {
            total: 0,
            per_day: [],
            zero_result_count: 0,
            zero_result_rate: 0,
            avg_top_score: null,
            avg_latency_ms: null,
            by_source: [],
          },
          top_queries: [],
          zero_result_queries: [],
        }),
      }),
    })
    renderPage(<KnowledgeAnalyticsPage />)

    expect(await screen.findByText(/no search activity yet/i)).toBeInTheDocument()
    expect(screen.queryByText('Total queries')).not.toBeInTheDocument()
  })

  it('blocks the page without reports:read', async () => {
    seedAuth(['knowledge:read'])
    mockFetch({
      'GET /api/v1/w/w1/knowledge/analytics': () => ({ body: makeAnalyticsOverview() }),
    })
    renderPage(<KnowledgeAnalyticsPage />)

    expect(
      await screen.findByText(/don’t have access to search analytics/i)
    ).toBeInTheDocument()
  })
})
