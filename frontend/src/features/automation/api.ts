import { api, ws } from '@/api/client'
import type { components } from '@/api/schema'

export type AutomationRule = components['schemas']['AutomationRuleOut']
export type AutomationRuleCreate = components['schemas']['AutomationRuleCreate']
export type AutomationRuleUpdate = components['schemas']['AutomationRuleUpdate']
export type Webhook = components['schemas']['WebhookOut']
export type WebhookCreate = components['schemas']['WebhookCreate']
export type WebhookUpdate = components['schemas']['WebhookUpdate']
export type WebhookDelivery = components['schemas']['WebhookDeliveryOut']

interface OffsetPage<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export const automationApi = {
  list: () => api.get<AutomationRule[]>(ws('/automations')),
  create: (body: AutomationRuleCreate) => api.post<AutomationRule>(ws('/automations'), body),
  update: (id: string, body: AutomationRuleUpdate) =>
    api.patch<AutomationRule>(ws(`/automations/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/automations/${id}`)),
  toggle: (id: string) => api.post<AutomationRule>(ws(`/automations/${id}/toggle`)),
  reorder: (orderedIds: string[]) =>
    api.post<AutomationRule[]>(ws('/automations/reorder'), { ordered_ids: orderedIds }),
}

export const webhooksApi = {
  list: () => api.get<Webhook[]>(ws('/webhooks')),
  create: (body: WebhookCreate) => api.post<Webhook>(ws('/webhooks'), body),
  update: (id: string, body: WebhookUpdate) => api.patch<Webhook>(ws(`/webhooks/${id}`), body),
  remove: (id: string) => api.delete<{ message: string }>(ws(`/webhooks/${id}`)),
  deliveries: (id: string, limit = 50) =>
    api.get<OffsetPage<WebhookDelivery>>(ws(`/webhooks/${id}/deliveries`), { query: { limit } }),
  test: (id: string) => api.post<WebhookDelivery>(ws(`/webhooks/${id}/test`)),
}
