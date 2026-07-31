import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { currentWorkspaceId } from '@/stores/auth'

import {
  automationApi,
  webhooksApi,
  type AutomationRule,
  type AutomationRuleCreate,
  type AutomationRuleUpdate,
  type WebhookCreate,
  type WebhookUpdate,
} from './api'

function errMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

// --- automation rules -------------------------------------------------------

export function useAutomationRules() {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: ['automation', workspaceId, 'rules'],
    queryFn: automationApi.list,
  })
}

function useRulesInvalidate() {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  return () => queryClient.invalidateQueries({ queryKey: ['automation', workspaceId, 'rules'] })
}

export function useCreateRule() {
  const invalidate = useRulesInvalidate()
  return useMutation({
    mutationFn: (body: AutomationRuleCreate) => automationApi.create(body),
    onSuccess: () => {
      toast.success('Rule created')
      void invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not create rule')),
  })
}

export function useUpdateRule() {
  const invalidate = useRulesInvalidate()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: AutomationRuleUpdate }) =>
      automationApi.update(id, body),
    onSuccess: () => {
      toast.success('Rule saved')
      void invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not save rule')),
  })
}

export function useToggleRule() {
  const invalidate = useRulesInvalidate()
  return useMutation({
    mutationFn: (id: string) => automationApi.toggle(id),
    onSuccess: () => void invalidate(),
    onError: (error) => toast.error(errMessage(error, 'Could not toggle rule')),
  })
}

export function useDeleteRule() {
  const invalidate = useRulesInvalidate()
  return useMutation({
    mutationFn: (id: string) => automationApi.remove(id),
    onSuccess: () => {
      toast.success('Rule deleted')
      void invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete rule')),
  })
}

export function useReorderRules() {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  return useMutation({
    mutationFn: (orderedIds: string[]) => automationApi.reorder(orderedIds),
    onMutate: async (orderedIds) => {
      const key = ['automation', workspaceId, 'rules']
      await queryClient.cancelQueries({ queryKey: key })
      const previous = queryClient.getQueryData<AutomationRule[]>(key)
      if (previous) {
        const byId = new Map(previous.map((r) => [r.id, r]))
        queryClient.setQueryData(
          key,
          orderedIds.map((id) => byId.get(id)).filter(Boolean)
        )
      }
      return { previous, key }
    },
    onError: (error, _ids, context) => {
      if (context?.previous) queryClient.setQueryData(context.key, context.previous)
      toast.error(errMessage(error, 'Could not reorder rules'))
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ['automation', workspaceId, 'rules'] }),
  })
}

// --- webhooks ---------------------------------------------------------------

export function useWebhooks() {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: ['automation', workspaceId, 'webhooks'],
    queryFn: webhooksApi.list,
  })
}

function useWebhooksInvalidate() {
  const queryClient = useQueryClient()
  const workspaceId = currentWorkspaceId()
  return () => queryClient.invalidateQueries({ queryKey: ['automation', workspaceId, 'webhooks'] })
}

export function useCreateWebhook() {
  const invalidate = useWebhooksInvalidate()
  return useMutation({
    mutationFn: (body: WebhookCreate) => webhooksApi.create(body),
    onSuccess: () => {
      toast.success('Webhook created')
      void invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not create webhook')),
  })
}

export function useUpdateWebhook() {
  const invalidate = useWebhooksInvalidate()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: WebhookUpdate }) => webhooksApi.update(id, body),
    onSuccess: () => {
      toast.success('Webhook saved')
      void invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not save webhook')),
  })
}

export function useDeleteWebhook() {
  const invalidate = useWebhooksInvalidate()
  return useMutation({
    mutationFn: (id: string) => webhooksApi.remove(id),
    onSuccess: () => {
      toast.success('Webhook deleted')
      void invalidate()
    },
    onError: (error) => toast.error(errMessage(error, 'Could not delete webhook')),
  })
}

export function useTestWebhook() {
  return useMutation({
    mutationFn: (id: string) => webhooksApi.test(id),
    onSuccess: (delivery) =>
      toast.success(
        delivery.status === 'success'
          ? `Test delivered (${delivery.response_code ?? 'ok'})`
          : `Test sent — status: ${delivery.status}`
      ),
    onError: (error) => toast.error(errMessage(error, 'Could not send test')),
  })
}

export function useWebhookDeliveries(id: string | null) {
  const workspaceId = currentWorkspaceId()
  return useQuery({
    queryKey: ['automation', workspaceId, 'webhooks', id, 'deliveries'],
    queryFn: () => webhooksApi.deliveries(id!),
    enabled: id !== null,
  })
}
