/**
 * AI engine API layer: providers/models, agents + custom actions, the sandbox
 * test endpoint, runs/traces and human-in-the-loop approvals. Shapes mirror
 * backend `schemas/ai_providers.py` and `schemas/agents.py`.
 */

import { api, ws } from '@/api/client'
import type { components } from '@/api/schema'

export type Provider = components['schemas']['AiProviderOut']
export type Model = components['schemas']['AiModelOut']
export type ModelFlat = components['schemas']['AiModelFlat']
export type CatalogModel = components['schemas']['CatalogModel']
export type ProviderTestResult = components['schemas']['ProviderTestResult']
export type Agent = components['schemas']['AgentOut']
export type CustomAction = components['schemas']['CustomActionOut']
export type ActionTestResult = components['schemas']['ActionTestResult']
export type AgentRun = components['schemas']['AgentRunOut']
export type AgentStep = components['schemas']['AgentStepOut']
export type AgentRunDetail = components['schemas']['AgentRunDetail']
export type Approval = components['schemas']['ApprovalOut']
export type AgentTestResult = components['schemas']['AgentTestResult']
export type Citation = components['schemas']['Citation']
export type RunsPage = components['schemas']['OffsetPage_AgentRunOut_']

export type ProviderKind = 'openai' | 'anthropic' | 'google' | 'openai_compatible' | 'ollama' | 'mock'
export type ToolPolicy = 'auto' | 'require_approval' | 'disabled'
export type AgentStatus = 'draft' | 'live' | 'off'
export type ToolConfig = { key: string; policy: ToolPolicy }

export interface AgentSettings {
  retrieval: { enabled: boolean; k: number; source_ids: string[] | null }
  handoff_message: string
  guardrails: { max_tool_calls: number; require_citations: boolean }
}

export const aiKeys = {
  catalog: (workspaceId: string) => ['ai', workspaceId, 'catalog'] as const,
  models: (workspaceId: string) => ['ai', workspaceId, 'models'] as const,
  providers: (workspaceId: string) => ['ai', workspaceId, 'providers'] as const,
  providerModels: (workspaceId: string, providerId: string) =>
    ['ai', workspaceId, 'provider-models', providerId] as const,
  agents: (workspaceId: string) => ['ai', workspaceId, 'agents'] as const,
  agent: (workspaceId: string, id: string) => ['ai', workspaceId, 'agent', id] as const,
  actions: (workspaceId: string) => ['ai', workspaceId, 'actions'] as const,
  runs: (workspaceId: string, filters?: Record<string, string | undefined>) =>
    ['ai', workspaceId, 'runs', filters ?? {}] as const,
  run: (workspaceId: string, id: string) => ['ai', workspaceId, 'run', id] as const,
  approvals: (workspaceId: string, status: string) =>
    ['ai', workspaceId, 'approvals', status] as const,
}

export const aiApi = {
  // --- providers + models --------------------------------------------------
  getCatalog: () => api.get<Record<string, CatalogModel[]>>(ws('/ai/catalog')),
  listModelsFlat: () => api.get<ModelFlat[]>(ws('/ai/models')),
  listProviders: () => api.get<Provider[]>(ws('/ai/providers')),
  createProvider: (body: {
    kind: ProviderKind
    name: string
    base_url?: string | null
    api_key?: string | null
    enabled?: boolean
  }) => api.post<Provider>(ws('/ai/providers'), body),
  updateProvider: (id: string, body: Record<string, unknown>) =>
    api.patch<Provider>(ws(`/ai/providers/${id}`), body),
  deleteProvider: (id: string) => api.delete<{ message: string }>(ws(`/ai/providers/${id}`)),
  testProvider: (id: string, modelKey?: string) =>
    api.post<ProviderTestResult>(ws(`/ai/providers/${id}/test`), { model_key: modelKey ?? null }),

  listProviderModels: (providerId: string) =>
    api.get<Model[]>(ws(`/ai/providers/${providerId}/models`)),
  createModel: (
    providerId: string,
    body: {
      model_key: string
      display_name?: string | null
      modality?: 'chat' | 'embedding'
      context_window?: number | null
      is_default?: boolean
    }
  ) => api.post<Model>(ws(`/ai/providers/${providerId}/models`), body),
  updateModel: (id: string, body: Record<string, unknown>) =>
    api.patch<Model>(ws(`/ai/models/${id}`), body),
  deleteModel: (id: string) => api.delete<{ message: string }>(ws(`/ai/models/${id}`)),
  setDefaultModel: (id: string) => api.post<Model>(ws(`/ai/models/${id}/set-default`)),

  // --- agents --------------------------------------------------------------
  listAgents: () => api.get<Agent[]>(ws('/ai/agents')),
  getAgent: (id: string) => api.get<Agent>(ws(`/ai/agents/${id}`)),
  createAgent: (body: Record<string, unknown>) => api.post<Agent>(ws('/ai/agents'), body),
  updateAgent: (id: string, body: Record<string, unknown>) =>
    api.patch<Agent>(ws(`/ai/agents/${id}`), body),
  deleteAgent: (id: string) => api.delete<{ message: string }>(ws(`/ai/agents/${id}`)),
  testAgent: (id: string, body: { message: string; history?: { role: string; content: string }[] }) =>
    api.post<AgentTestResult>(ws(`/ai/agents/${id}/test`), body),

  // --- custom actions ------------------------------------------------------
  listActions: () => api.get<CustomAction[]>(ws('/ai/actions')),
  createAction: (body: Record<string, unknown>) => api.post<CustomAction>(ws('/ai/actions'), body),
  updateAction: (id: string, body: Record<string, unknown>) =>
    api.patch<CustomAction>(ws(`/ai/actions/${id}`), body),
  deleteAction: (id: string) => api.delete<{ message: string }>(ws(`/ai/actions/${id}`)),
  testAction: (id: string, params: Record<string, unknown>) =>
    api.post<ActionTestResult>(ws(`/ai/actions/${id}/test`), { params }),

  // --- runs ----------------------------------------------------------------
  listRuns: (filters: { agent_id?: string; conversation_id?: string; status?: string; offset?: number }) =>
    api.get<RunsPage>(ws('/ai/runs'), {
      query: { ...filters, limit: 30 },
    }),
  getRun: (id: string) => api.get<AgentRunDetail>(ws(`/ai/runs/${id}`)),

  // --- approvals -----------------------------------------------------------
  listApprovals: (status = 'pending') =>
    api.get<Approval[]>(ws('/ai/approvals'), { query: { status } }),
  decideApproval: (id: string, body: { approved: boolean; note?: string | null }) =>
    api.post<Approval>(ws(`/ai/approvals/${id}/decide`), body),
}
