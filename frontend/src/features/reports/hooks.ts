import { useQuery } from '@tanstack/react-query'

import { currentWorkspaceId } from '@/stores/auth'

import { reportsApi } from './api'

export function useReportOverview(days: number) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: ['reports', workspaceId, 'overview', days],
    queryFn: () => reportsApi.overview(days),
  })
}
