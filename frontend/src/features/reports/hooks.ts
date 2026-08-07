import { useQuery } from '@tanstack/react-query'

import { currentWorkspaceId } from '@/stores/auth'

import { reportsApi, reportsExtraApi } from './api'

export function useReportOverview(days: number) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: ['reports', workspaceId, 'overview', days],
    queryFn: () => reportsApi.overview(days),
  })
}

export function useBreakdown(dimension: string, days: number) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: ['reports', workspaceId, 'breakdown', dimension, days],
    queryFn: () => reportsExtraApi.breakdown(dimension, days),
  })
}

export function useSlaReport(days: number) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: ['reports', workspaceId, 'sla', days],
    queryFn: () => reportsExtraApi.sla(days),
  })
}
