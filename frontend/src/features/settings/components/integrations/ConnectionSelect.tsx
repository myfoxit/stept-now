/**
 * Select over the workspace's OAuth connections for one provider. When the
 * workspace has none (or the viewer cannot list integrations), renders a
 * "Connect …" deep link into Settings → Integrations instead.
 */

import { ExternalLink } from 'lucide-react'
import { Link } from 'react-router'

import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'

import { useIntegrations } from '../../hooks'
import { t } from '@/i18n'

/** Marketing-ish display names for deep-link copy, keyed by provider id. */
const PROVIDER_NAMES: Record<string, string> = {
  google: 'Google',
  microsoft: 'Microsoft',
  notion: 'Notion',
  confluence: 'Confluence',
  slack: 'Slack',
}

export function ConnectionSelect({
  id,
  providerId,
  label,
  value,
  onChange,
  hint,
}: {
  id: string
  providerId: string
  label: string
  value: string
  onChange: (connectionId: string) => void
  hint?: string
}) {
  const integrations = useIntegrations()
  const provider = integrations.data?.providers.find((p) => p.id === providerId)
  const connections = (provider?.connections ?? []).filter((c) => c.status !== 'revoked')
  const name = provider?.name ?? PROVIDER_NAMES[providerId] ?? providerId

  if (!integrations.isLoading && connections.length === 0) {
    return (
      <div className="grid gap-1.5">
        <Label>{label}</Label>
        <div className="flex items-center justify-between gap-3 rounded-md border border-dashed px-3 py-2">
          <p className="text-xs text-muted-foreground">
            No {name} account is connected to this workspace yet.
          </p>
          <Link
            to="/settings/integrations"
            className="inline-flex shrink-0 items-center gap-1 text-xs font-medium underline underline-offset-2 hover:text-foreground"
          >
            Connect {name} <ExternalLink className="size-3" />
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <NativeSelect
        id={id}
        className="w-full"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <NativeSelectOption value="">{t('settings.choose_an_account')}</NativeSelectOption>
        {connections.map((connection) => (
          <NativeSelectOption key={connection.id} value={connection.id}>
            {connection.account_label ?? connection.id}
            {connection.status === 'reauth_required' ? ' (needs reauthorization)' : ''}
          </NativeSelectOption>
        ))}
      </NativeSelect>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}
