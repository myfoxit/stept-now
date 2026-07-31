/** TanStack Query hooks for the AI engine (providers, agents, runs, approvals). */

import { useQuery, useQueryClient } from '@tanstack/react-query'

import { currentWorkspaceId } from '@/stores/auth'

import { aiApi, aiKeys } from './api'

export function useProviders() {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: aiKeys.providers(workspaceId), queryFn: aiApi.listProviders })
}

export function useCatalog() {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: aiKeys.catalog(workspaceId), queryFn: aiApi.getCatalog })
}

export function useModelsFlat() {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: aiKeys.models(workspaceId), queryFn: aiApi.listModelsFlat })
}

export function useProviderModels(providerId: string) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: aiKeys.providerModels(workspaceId, providerId),
    queryFn: () => aiApi.listProviderModels(providerId),
    enabled: Boolean(providerId),
  })
}

export function useAgents() {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: aiKeys.agents(workspaceId), queryFn: aiApi.listAgents })
}

export function useAgent(id: string) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: aiKeys.agent(workspaceId, id),
    queryFn: () => aiApi.getAgent(id),
    enabled: Boolean(id),
  })
}

export function useActions() {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: aiKeys.actions(workspaceId), queryFn: aiApi.listActions })
}

export function useRuns(filters: { agent_id?: string; conversation_id?: string; status?: string }) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: aiKeys.runs(workspaceId, filters),
    queryFn: () => aiApi.listRuns(filters),
  })
}

export function useRun(id: string) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: aiKeys.run(workspaceId, id),
    queryFn: () => aiApi.getRun(id),
    enabled: Boolean(id),
    // Poll while the run is still in flight so the trace fills in live.
    refetchInterval: (query) =>
      ['queued', 'running', 'awaiting_approval'].includes(query.state.data?.run.status ?? '')
        ? 2500
        : false,
  })
}

export function useApprovals(status = 'pending') {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: aiKeys.approvals(workspaceId, status),
    queryFn: () => aiApi.listApprovals(status),
  })
}

export function useInvalidateAi() {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ['ai', workspaceId] })
}
