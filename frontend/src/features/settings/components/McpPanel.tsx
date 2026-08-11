/**
 * Settings → MCP · AI clients: one-click key setup for Claude/Cursor/ChatGPT
 * plus the workspace endpoint and the existing API-key table (MCP auth reuses
 * workspace API keys; agent-bound keys are flagged).
 */

import { Bot, KeyRound, Plus, Settings2, TriangleAlert } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { fullDateTime } from '@/lib/format'

import { useApiKeys, useCreateApiKey, useRevokeApiKey } from '../hooks'
import { MCP_CLIENTS, McpSnippets, SnippetBlock, mcpOrigin } from './McpSnippets'
import { t } from '@/i18n'

const SCOPES = [
  { value: 'read', label: 'Read', hint: 'Read-only access' },
  { value: 'write', label: 'Write', hint: 'Create & update' },
  { value: 'admin', label: 'Admin', hint: 'Full workspace access' },
]

export function McpPanel() {
  const keys = useApiKeys()
  const create = useCreateApiKey()
  const revoke = useRevokeApiKey()

  const [client, setClient] = useState(MCP_CLIENTS[0].value)
  const [rawKey, setRawKey] = useState<string | null>(null)

  const [customizeOpen, setCustomizeOpen] = useState(false)
  const [customName, setCustomName] = useState('')
  const [customScopes, setCustomScopes] = useState<string[]>(['read', 'write'])

  const endpoint = `${mcpOrigin()}/mcp`
  const selectedClient = MCP_CLIENTS.find((c) => c.value === client) ?? MCP_CLIENTS[0]

  async function createKey(name: string, scopes: string[]) {
    try {
      const created = await create.mutateAsync({ name, scopes })
      setRawKey(created.key)
      setCustomizeOpen(false)
    } catch {
      /* toast handled in hook */
    }
  }

  function toggleCustomScope(scope: string) {
    setCustomScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    )
  }

  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted-foreground">
        Connect Claude, Cursor or any MCP client to this workspace. Clients search your knowledge,
        conversations and tours with an API key; every tool call respects the key&rsquo;s scopes and
        is audited.
      </p>

      {/* Endpoint */}
      <div className="grid gap-1.5">
        <Label>{t('settings.mcp_endpoint')}</Label>
        <SnippetBlock value={endpoint} copyLabel="Copy MCP endpoint" />
      </div>

      {/* Install + one-click key */}
      <Card>
        <CardContent className="grid gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-medium">{t('common.install_in_your_client')}</h3>
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                onClick={() => createKey(selectedClient.label, ['read', 'write'])}
                disabled={create.isPending}
              >
                <Plus className="size-4" />
                {create.isPending ? 'Creating…' : 'Create key for this client'}
              </Button>
              <Popover
                open={customizeOpen}
                onOpenChange={(open) => {
                  setCustomizeOpen(open)
                  if (open) {
                    setCustomName(selectedClient.label)
                    setCustomScopes(['read', 'write'])
                  }
                }}
              >
                <PopoverTrigger asChild>
                  <Button size="sm" variant="outline" aria-label={t('settings.customize_key_before_creating')}>
                    <Settings2 className="size-4" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent align="end" className="w-72">
                  <div className="grid gap-3">
                    <div className="grid gap-1.5">
                      <Label htmlFor="mcp-key-name">{t('settings.key_name')}</Label>
                      <Input
                        id="mcp-key-name"
                        value={customName}
                        onChange={(e) => setCustomName(e.target.value)}
                        placeholder={selectedClient.label}
                      />
                    </div>
                    <div className="grid gap-2">
                      <Label>{t('settings.scopes')}</Label>
                      {SCOPES.map((scope) => (
                        <label
                          key={scope.value}
                          className="flex items-center gap-2 text-sm"
                          htmlFor={`mcp-scope-${scope.value}`}
                        >
                          <Checkbox
                            id={`mcp-scope-${scope.value}`}
                            checked={customScopes.includes(scope.value)}
                            onCheckedChange={() => toggleCustomScope(scope.value)}
                          />
                          <span className="font-medium">{scope.label}</span>
                          <span className="text-xs text-muted-foreground">{scope.hint}</span>
                        </label>
                      ))}
                    </div>
                    <Button
                      size="sm"
                      onClick={() => createKey(customName.trim() || selectedClient.label, customScopes)}
                      disabled={customScopes.length === 0 || create.isPending}
                    >
                      {create.isPending ? 'Creating…' : 'Create key'}
                    </Button>
                  </div>
                </PopoverContent>
              </Popover>
            </div>
          </div>

          <McpSnippets
            endpointUrl={endpoint}
            rawKey={rawKey ?? undefined}
            value={client}
            onValueChange={setClient}
          />

          {rawKey ? (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
              <p className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
                <TriangleAlert className="size-4 shrink-0" />
                {t('common.this_key_is_shown_once_copy')}
              </p>
              <Button size="sm" variant="outline" onClick={() => setRawKey(null)}>
                {t('common.done')}
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* Existing keys */}
      {keys.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : !keys.data || keys.data.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Bot />
            </EmptyMedia>
            <EmptyTitle>{t('settings.no_keys_yet')}</EmptyTitle>
            <EmptyDescription>
              {t('settings.create_a_key_above_to_connect')}
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <Card>
          <CardContent className="overflow-x-auto p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('common.name')}</TableHead>
                  <TableHead>{t('settings.prefix')}</TableHead>
                  <TableHead>{t('settings.scopes')}</TableHead>
                  <TableHead>{t('settings.last_used')}</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.data.map((key) => (
                  <TableRow key={key.id} className={key.revoked_at ? 'opacity-50' : undefined}>
                    <TableCell className="font-medium">
                      <span className="inline-flex items-center gap-2">
                        <KeyRound className="size-3.5 text-muted-foreground" />
                        {key.name}
                        {key.agent_id ? (
                          <Badge variant="outline" className="text-[10px] text-muted-foreground">
                            {t('common.agent_bound')}
                          </Badge>
                        ) : null}
                      </span>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{key.prefix}…</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {key.scopes.map((scope) => (
                          <Badge key={scope} variant="secondary" className="capitalize">
                            {scope}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {key.last_used_at ? fullDateTime(key.last_used_at) : 'Never'}
                    </TableCell>
                    <TableCell>
                      {key.revoked_at ? (
                        <Badge variant="outline">{t('settings.revoked')}</Badge>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={revoke.isPending}
                          onClick={() => revoke.mutate(key.id)}
                        >
                          {t('settings.revoke')}
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
