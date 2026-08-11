/** SLA block for the conversation context pane: policy, status, breaches, apply/remove. */

import { Badge } from '@/components/ui/badge'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { timeAgo } from '@/lib/format'
import { useHasPerm } from '@/stores/auth'

import { useApplySla, useConversationSla, useSlaPolicyOptions } from '@/features/inbox/hooks'
import { t } from '@/i18n'

const NONE = '__none__'

function SlaStatusBadge({ status }: { status: string }) {
  const label = status.replace(/_/g, ' ')
  if (status === 'hit') {
    return (
      <Badge className="border-transparent bg-emerald-600 text-white capitalize dark:bg-emerald-700">
        {label}
      </Badge>
    )
  }
  if (status === 'missed' || status === 'active_with_misses') {
    return (
      <Badge variant="destructive" className="capitalize">
        {label}
      </Badge>
    )
  }
  return (
    <Badge variant="secondary" className="capitalize">
      {label}
    </Badge>
  )
}

export function SlaCard({ conversationId }: { conversationId: string }) {
  const canManage = useHasPerm('conversations:manage')
  const sla = useConversationSla(conversationId)
  const policies = useSlaPolicyOptions(canManage)
  const apply = useApplySla(conversationId)

  if (sla.isLoading) return <Skeleton className="h-16 w-full" data-testid="sla-loading" />
  if (sla.isError) {
    return <p className="text-xs text-muted-foreground">{t('inbox.could_not_load_sla')}</p>
  }

  const policy = sla.data?.policy ?? null
  const status = sla.data?.status ?? null
  const events = sla.data?.events ?? []

  return (
    <div className="space-y-2" data-testid="sla-card">
      {policy ? (
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium">{policy.name}</span>
          {status ? <SlaStatusBadge status={status} /> : null}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">{t('inbox.no_sla_applied')}</p>
      )}

      {events.length > 0 ? (
        <ul className="space-y-1" data-testid="sla-events">
          {events.map((event) => (
            <li
              key={event.id}
              className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground"
            >
              <span className="uppercase tracking-wide">{event.event_type.replace(/_/g, ' ')}</span>
              <span>{timeAgo(event.created_at)}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {canManage ? (
        <NativeSelect
          aria-label={t('inbox.apply_sla')}
          className="w-full"
          value={policy?.id ?? NONE}
          disabled={apply.isPending}
          onChange={(e) => apply.mutate(e.target.value === NONE ? null : e.target.value)}
        >
          <NativeSelectOption value={NONE}>No SLA</NativeSelectOption>
          {(policies.data ?? []).map((p) => (
            <NativeSelectOption key={p.id} value={p.id}>
              {p.name}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      ) : null}
    </div>
  )
}
