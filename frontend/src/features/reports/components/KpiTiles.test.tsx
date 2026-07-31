import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { renderApp } from '@/test/helpers'

import type { ReportTotals } from '../api'
import { KpiTiles } from './KpiTiles'

const totals: ReportTotals = {
  new_conversations: 1284,
  resolved_conversations: 940,
  resolution_rate: 0.732,
  median_first_response_minutes: 42,
  median_resolution_minutes: 130,
  csat_avg: 4.6,
  csat_count: 88,
  ai_runs: 200,
  ai_resolved: 150,
  ai_resolution_rate: 0.75,
}

describe('KpiTiles', () => {
  it('renders KPI values from a mocked overview totals object', () => {
    renderApp(<KpiTiles totals={totals} />)
    expect(screen.getByText('New conversations')).toBeInTheDocument()
    expect(screen.getByText('1.3K')).toBeInTheDocument() // compacted new_conversations
    expect(screen.getByText('73%')).toBeInTheDocument() // resolution rate
    expect(screen.getByText('42m')).toBeInTheDocument() // median first response
    expect(screen.getByText('4.6 / 5')).toBeInTheDocument() // CSAT
    expect(screen.getByText('75%')).toBeInTheDocument() // AI resolution rate
  })

  it('shows an em dash for a missing CSAT average', () => {
    renderApp(<KpiTiles totals={{ ...totals, csat_avg: null, csat_count: 0 }} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })
})
