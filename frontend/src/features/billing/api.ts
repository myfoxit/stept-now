/**
 * Billing API — hand-written mirrors of `app/schemas/billing.py` (this wave
 * lands before `make types` regenerates `@/api/schema`; swap these for
 * `components['schemas']['BillingOut']` & friends once it runs).
 *
 * On self-hosted installs billing is disabled server-side: `billing_enabled`
 * is false and the checkout/portal endpoints answer 409 — the UI never calls
 * them then.
 */

import { api, ws } from '@/api/client'

export type BillingPlan = 'free' | 'cloud' | 'business'
export type PaidPlan = Exclude<BillingPlan, 'free'>

export interface Billing {
  plan: BillingPlan
  status: string
  seats: number
  member_count: number
  current_period_end: string | null
  cancel_at_period_end: boolean
  ai_runs_this_period: number
  included_ai_runs: number
  billing_enabled: boolean
  publishable_key: string | null
}

export interface CheckoutSession {
  url: string
}

export interface PortalSession {
  url: string
}

export const billingKeys = {
  all: (workspaceId: string) => ['billing', workspaceId] as const,
}

export const billingApi = {
  get: () => api.get<Billing>(ws('/billing')),
  createCheckoutSession: (plan: PaidPlan) =>
    api.post<CheckoutSession>(ws('/billing/checkout-session'), { plan }),
  createPortalSession: () => api.post<PortalSession>(ws('/billing/portal-session')),
}
