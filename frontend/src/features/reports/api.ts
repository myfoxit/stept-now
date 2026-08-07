import { api, ws } from '@/api/client'
import type { components } from '@/api/schema'

export type ReportOverview = components['schemas']['ReportOverview']
export type ReportTotals = components['schemas']['ReportTotals']
export type ReportByDay = components['schemas']['ReportByDay']
export type ReportByChannel = components['schemas']['ReportByChannel']
export type ReportByAgent = components['schemas']['ReportByAgent']

export const reportsApi = {
  overview: (days: number) =>
    api.get<ReportOverview>(ws('/reports/overview'), { query: { days } }),
}

// ---------------------------------------------------------------------------
// Dimension breakdowns, SLA attainment, CSV (docs/CHATWOOT-BACKLOG.md §1.5)
// ---------------------------------------------------------------------------

export type ReportBreakdown = components['schemas']['ReportBreakdown']
export type ReportDimensionRow = components['schemas']['ReportDimensionRow']
export type SlaReport = components['schemas']['SlaReport']

export const DIMENSIONS = [
  { value: 'agent', label: 'Agent' },
  { value: 'team', label: 'Team' },
  { value: 'inbox', label: 'Inbox' },
  { value: 'tag', label: 'Tag' },
  { value: 'channel', label: 'Channel' },
] as const

export const reportsExtraApi = {
  breakdown: (dimension: string, days: number) =>
    api.get<ReportBreakdown>(ws('/reports/breakdown'), { query: { dimension, days } }),
  sla: (days: number) => api.get<SlaReport>(ws('/reports/sla'), { query: { days } }),
  csvUrl: (kind: 'overview' | 'breakdown' | 'sla', params: Record<string, string | number>) => {
    const query = new URLSearchParams(
      Object.entries(params).map(([key, value]) => [key, String(value)])
    )
    return `${ws(`/reports/${kind}.csv`)}?${query.toString()}`
  },
}
