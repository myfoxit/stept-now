import { useMutation, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
// The jsdom-safe full-page-redirect seam the OAuth flows already use.
import { assignLocation } from '@/features/settings/components/integrations/redirect'
import { currentWorkspaceId } from '@/stores/auth'

import { billingApi, billingKeys, type PaidPlan } from './api'

function errMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

export function useBilling() {
  const workspaceId = currentWorkspaceId()
  return useQuery({ queryKey: billingKeys.all(workspaceId), queryFn: billingApi.get })
}

/** Starts Stripe Checkout — on success the browser leaves for stripe.com. */
export function useStartCheckout() {
  return useMutation({
    mutationFn: (plan: PaidPlan) => billingApi.createCheckoutSession(plan),
    onSuccess: (data) => assignLocation(data.url),
    onError: (error) => toast.error(errMessage(error, 'Could not start checkout')),
  })
}

/** Opens the Stripe Billing Portal (invoices, payment method, cancel/resume). */
export function useOpenBillingPortal() {
  return useMutation({
    mutationFn: () => billingApi.createPortalSession(),
    onSuccess: (data) => assignLocation(data.url),
    onError: (error) => toast.error(errMessage(error, 'Could not open the billing portal')),
  })
}
