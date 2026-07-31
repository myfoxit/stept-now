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
