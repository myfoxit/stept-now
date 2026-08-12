import { useQueryClient } from '@tanstack/react-query'
import { Check, CreditCard, ExternalLink } from 'lucide-react'
import { useEffect } from 'react'
import { useSearchParams } from 'react-router'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { billingKeys, type Billing, type BillingPlan, type PaidPlan } from '@/features/billing/api'
import { useBilling, useOpenBillingPortal, useStartCheckout } from '@/features/billing/hooks'
import { fullDateTime } from '@/lib/format'
import { currentWorkspaceId, useHasPerm } from '@/stores/auth'
import { t } from '@/i18n'

/** Display mirror of the backend PLAN_CATALOG (services/billing.py). */
const PLANS: Array<{
  key: BillingPlan
  name: string
  price: string
  per: string
  features: string[]
}> = [
  {
    key: 'free',
    name: 'Free',
    price: '$0',
    per: 'forever',
    features: ['All channels', 'Bring your own model key'],
  },
  {
    key: 'cloud',
    name: 'Cloud',
    price: '$19',
    per: 'per seat / month',
    features: ['500 AI runs / month', 'Managed hosting & backups'],
  },
  {
    key: 'business',
    name: 'Business',
    price: '$49',
    per: 'per seat / month',
    features: ['2,000 AI runs / month', 'Custom roles', 'Audit log', 'SLA management'],
  },
]

function statusVariant(status: string): 'secondary' | 'destructive' | 'outline' {
  if (status === 'past_due' || status === 'incomplete') return 'destructive'
  if (status === 'canceled') return 'outline'
  return 'secondary'
}

function CurrentPlanCard({ billing }: { billing: Billing }) {
  const plan = PLANS.find((p) => p.key === billing.plan)
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-semibold">{plan?.name ?? billing.plan}</span>
        <Badge variant={statusVariant(billing.status)}>{billing.status.replace('_', ' ')}</Badge>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        {billing.member_count} {billing.member_count === 1 ? 'member' : 'members'}
        {billing.plan !== 'free' ? <> · {billing.seats} paid seats</> : null}
      </p>
      {billing.current_period_end ? (
        <p className="mt-1 text-sm text-muted-foreground">
          {billing.cancel_at_period_end ? 'Cancels on' : 'Renews'}{' '}
          {fullDateTime(billing.current_period_end)}
        </p>
      ) : null}
    </Card>
  )
}

function UsageMeter({ billing }: { billing: Billing }) {
  if (billing.included_ai_runs <= 0) {
    return (
      <Card className="p-4">
        <p className="text-sm font-medium">{t('settings.ai_usage')}</p>
        <p className="mt-1 text-sm text-muted-foreground">
          {/* `count`, not `runs`: `t` keys plural selection on that name, and a
              free-plan workspace really does pass through exactly one run. */}
          {t('settings.ai_runs_this_month_no_runs', { count: billing.ai_runs_this_period })}
        </p>
      </Card>
    )
  }
  const percent = Math.min(100, (billing.ai_runs_this_period / billing.included_ai_runs) * 100)
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">{t('settings.ai_usage')}</span>
        <span className="text-muted-foreground">
          {billing.ai_runs_this_period} / {billing.included_ai_runs} AI runs this month
        </span>
      </div>
      <Progress
        className="mt-3"
        value={percent}
        aria-label={`${billing.ai_runs_this_period} of ${billing.included_ai_runs} included AI runs used`}
      />
    </Card>
  )
}

export function BillingPanel() {
  const canManage = useHasPerm('workspace:manage')
  const billing = useBilling()
  const checkout = useStartCheckout()
  const portal = useOpenBillingPortal()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()

  // Stripe Checkout lands back here with ?checkout=success|canceled.
  const checkoutResult = searchParams.get('checkout')
  useEffect(() => {
    if (!checkoutResult) return
    if (checkoutResult === 'success') {
      toast.success(t('settings.subscription_updated'))
      void queryClient.invalidateQueries({ queryKey: billingKeys.all(currentWorkspaceId()) })
    } else if (checkoutResult === 'canceled') {
      toast('Checkout canceled — nothing changed')
    }
    const next = new URLSearchParams(searchParams)
    next.delete('checkout')
    setSearchParams(next, { replace: true })
    // searchParams identity churns on every navigation; the param is the real input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkoutResult])

  if (billing.isLoading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }

  if (billing.isError || !billing.data) {
    return (
      <Card className="p-6 text-center text-sm text-muted-foreground">
        Could not load billing.{' '}
        <Button variant="link" className="px-1" onClick={() => billing.refetch()}>
          {t('common.retry')}
        </Button>
      </Card>
    )
  }

  const data = billing.data

  if (!data.billing_enabled) {
    return (
      <Card className="flex flex-row items-start gap-3 p-6">
        <CreditCard className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
        <div>
          <p className="font-medium">{t('settings.billing_is_not_enabled_on_this')}</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {t('settings.self_hosted_stept_includes_every_feature')}
          </p>
        </div>
      </Card>
    )
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {t('settings.your_plan_seats_and_ai_usage')}
        </p>
        {canManage && data.plan !== 'free' ? (
          <Button
            size="sm"
            variant="outline"
            disabled={portal.isPending}
            onClick={() => portal.mutate()}
          >
            <ExternalLink className="size-4" />
            {portal.isPending ? 'Opening…' : 'Manage billing'}
          </Button>
        ) : null}
      </div>

      <CurrentPlanCard billing={data} />
      <UsageMeter billing={data} />

      <div className="grid gap-3 md:grid-cols-3">
        {PLANS.map((plan) => {
          const isCurrent = plan.key === data.plan
          return (
            <Card key={plan.key} className="flex flex-col gap-3 p-4">
              <div className="flex items-center justify-between">
                <span className="font-medium">{plan.name}</span>
                {isCurrent ? <Badge variant="secondary">{t('settings.current_plan')}</Badge> : null}
              </div>
              <p>
                <span className="text-2xl font-semibold">{plan.price}</span>{' '}
                <span className="text-xs text-muted-foreground">{plan.per}</span>
              </p>
              <ul className="grid gap-1 text-sm text-muted-foreground">
                {plan.features.map((feature) => (
                  <li key={feature} className="flex items-center gap-2">
                    <Check className="size-3.5 shrink-0 text-brand" />
                    {feature}
                  </li>
                ))}
              </ul>
              {canManage && !isCurrent && plan.key !== 'free' ? (
                <Button
                  className="mt-auto"
                  size="sm"
                  disabled={checkout.isPending}
                  onClick={() => checkout.mutate(plan.key as PaidPlan)}
                >
                  {checkout.isPending ? 'Redirecting…' : `Upgrade to ${plan.name}`}
                </Button>
              ) : null}
            </Card>
          )
        })}
      </div>

      <p className="text-xs text-muted-foreground">
        {t('settings.plan_changes_and_cancellation_are_handled')}
      </p>
    </div>
  )
}
