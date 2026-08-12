/**
 * One provider card: name + description + state chip, Connect action,
 * per-connection rows (status, Reconnect, Disconnect with confirm) and the
 * "Use your own app" credential collapsible.
 */

import { ChevronDown, RefreshCw, TriangleAlert, Unplug } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'

import type { IntegrationConnection, IntegrationProvider } from '../../api'
import {
  useConnectIntegration,
  useDisconnectConnection,
  useReconnectConnection,
} from '../../hooks'
import { CredentialForm } from './CredentialForm'
import { activeConnections, providerIcon, providerState } from './lib'
import { t } from '@/i18n'

function StateChip({ provider }: { provider: IntegrationProvider }) {
  const state = providerState(provider)
  if (state === 'connected') {
    return <Badge variant="secondary">Connected ({activeConnections(provider).length})</Badge>
  }
  if (state === 'needs_setup') return <Badge variant="outline">{t('settings.needs_setup')}</Badge>
  return <Badge variant="outline">{t('settings.not_connected')}</Badge>
}

function ConnectionStatusBadge({ connection }: { connection: IntegrationConnection }) {
  if (connection.status === 'reauth_required') {
    return (
      <Badge
        variant="outline"
        className="gap-1 border-amber-500/40 text-amber-600 dark:text-amber-400"
      >
        <TriangleAlert className="size-3" /> {t('settings.reauthorize')}
      </Badge>
    )
  }
  if (connection.status === 'error') return <Badge variant="destructive">{t('common.error')}</Badge>
  return <Badge variant="secondary">{t('settings.connected')}</Badge>
}

export function IntegrationCard({ provider }: { provider: IntegrationProvider }) {
  const connect = useConnectIntegration()
  const reconnect = useReconnectConnection()
  const disconnect = useDisconnectConnection()
  const [disconnecting, setDisconnecting] = useState<IntegrationConnection | null>(null)
  const Icon = providerIcon(provider)

  const connections = activeConnections(provider)
  const isZendesk = provider.id === 'zendesk'
  const canConnect = provider.auth === 'oauth2' && provider.configured

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-md border bg-muted/50">
              <Icon className="size-4 text-muted-foreground" />
            </div>
            <div className="grid gap-0.5">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-medium">{provider.name}</h3>
                <StateChip provider={provider} />
              </div>
              <p className="text-xs text-muted-foreground">{provider.description}</p>
            </div>
          </div>
          {provider.auth === 'oauth2' ? (
            <Button
              size="sm"
              disabled={!canConnect || connect.isPending}
              title={canConnect ? undefined : 'Add app credentials below first'}
              onClick={() => connect.mutate({ provider: provider.id })}
            >
              {connect.isPending ? 'Connecting…' : 'Connect'}
            </Button>
          ) : null}
        </div>
      </CardHeader>

      {connections.length > 0 || provider.credential || isZendesk ? (
        <CardContent className="grid gap-3">
          {isZendesk ? (
            <p className="text-xs text-muted-foreground">
              Zendesk uses an API token instead of OAuth — add your help center as a knowledge
              source under{' '}
              <Link to="/knowledge" className="underline underline-offset-2 hover:text-foreground">
                {t('settings.knowledge_add_source_zendesk')}
              </Link>
              .
            </p>
          ) : null}

          {connections.length > 0 ? (
            <ul className="grid gap-2">
              {connections.map((connection) => (
                <li
                  key={connection.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm">
                      {connection.account_label ?? provider.name}
                    </span>
                    <ConnectionStatusBadge connection={connection} />
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={reconnect.isPending}
                      aria-label={`Reconnect ${connection.account_label ?? provider.name}`}
                      onClick={() => reconnect.mutate({ connectionId: connection.id })}
                    >
                      <RefreshCw className="size-3.5" /> {t('settings.reconnect')}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Disconnect ${connection.account_label ?? provider.name}`}
                      onClick={() => setDisconnecting(connection)}
                    >
                      <Unplug className="size-3.5" /> {t('settings.disconnect')}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          ) : null}

          {provider.credential ? (
            <Collapsible defaultOpen={providerState(provider) === 'needs_setup'}>
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm" className="-ml-2 text-muted-foreground">
                  <ChevronDown className="size-3.5" /> {t('settings.use_your_own_app')}
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="pt-2">
                <CredentialForm provider={provider} credential={provider.credential} />
              </CollapsibleContent>
            </Collapsible>
          ) : null}
        </CardContent>
      ) : null}

      <AlertDialog
        open={disconnecting !== null}
        onOpenChange={(open) => !open && setDisconnecting(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Disconnect “{disconnecting?.account_label ?? provider.name}”?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('settings.channels_and_knowledge_sources_using_this', { provider: provider.name })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (disconnecting) disconnect.mutate(disconnecting.id)
                setDisconnecting(null)
              }}
            >
              {t('settings.disconnect')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}
