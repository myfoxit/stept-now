/**
 * Settings → Integrations: the provider catalog grouped by category, with
 * one-click OAuth connect, per-connection management and "use your own app"
 * credential forms. Handles the ?connected= / ?error= params the OAuth
 * callback redirects back with (toast once, then strip them from the URL).
 */

import { useQueryClient } from '@tanstack/react-query'
import { Blocks } from 'lucide-react'
import { useEffect } from 'react'
import { useSearchParams } from 'react-router'
import { toast } from 'sonner'

import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Skeleton } from '@/components/ui/skeleton'
import { currentWorkspaceId } from '@/stores/auth'

import { integrationsKeys } from '../api'
import { useIntegrations } from '../hooks'
import { IntegrationCard } from './integrations/IntegrationCard'
import { CATEGORY_LABELS, CATEGORY_ORDER, oauthErrorMessage } from './integrations/lib'

export function IntegrationsPanel() {
  const integrations = useIntegrations()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()

  // OAuth callback landing: toast the outcome once, drop the params, refetch.
  const connected = searchParams.get('connected')
  const error = searchParams.get('error')
  useEffect(() => {
    if (!connected && !error) return
    if (connected) {
      toast.success(`${connected.charAt(0).toUpperCase()}${connected.slice(1)} connected`)
      void queryClient.invalidateQueries({
        queryKey: integrationsKeys.all(currentWorkspaceId()),
      })
    }
    if (error) toast.error(oauthErrorMessage(error))
    const next = new URLSearchParams(searchParams)
    next.delete('connected')
    next.delete('error')
    setSearchParams(next, { replace: true })
    // searchParams identity churns on every navigation; the params above are the real inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, error])

  if (integrations.isLoading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    )
  }

  const providers = integrations.data?.providers ?? []
  if (providers.length === 0) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Blocks />
          </EmptyMedia>
          <EmptyTitle>No integrations available</EmptyTitle>
          <EmptyDescription>
            {integrations.isError
              ? 'Integrations could not be loaded — try again shortly.'
              : 'This instance ships no integration providers.'}
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  return (
    <div className="grid gap-6">
      <p className="text-sm text-muted-foreground">
        Connect the accounts Stept sends mail through, syncs knowledge from and posts to.
        Connections belong to the workspace and can be managed by any admin.
      </p>
      {CATEGORY_ORDER.map((category) => {
        const group = providers.filter((p) => p.category === category)
        if (group.length === 0) return null
        return (
          <section key={category} aria-label={CATEGORY_LABELS[category]} className="grid gap-3">
            <h2 className="text-sm font-semibold">{CATEGORY_LABELS[category]}</h2>
            <div className="grid gap-3 xl:grid-cols-2">
              {group.map((provider) => (
                <IntegrationCard key={provider.id} provider={provider} />
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
