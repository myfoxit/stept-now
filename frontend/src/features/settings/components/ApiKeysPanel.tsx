import { Copy, KeyRound, Plus, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
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

import type { ApiKeyCreated } from '../api'
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from '../hooks'
import { t } from '@/i18n'

const SCOPES = [
  { value: 'read', label: 'Read', hint: 'Read-only access' },
  { value: 'write', label: 'Write', hint: 'Create & update' },
  { value: 'admin', label: 'Admin', hint: 'Full workspace access' },
]

export function ApiKeysPanel() {
  const keys = useApiKeys()
  const create = useCreateApiKey()
  const revoke = useRevokeApiKey()

  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<string[]>(['read'])
  const [created, setCreated] = useState<ApiKeyCreated | null>(null)

  function toggleScope(scope: string) {
    setScopes((prev) => (prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]))
  }

  async function submit() {
    if (!name.trim() || scopes.length === 0) return
    try {
      const key = await create.mutateAsync({ name: name.trim(), scopes })
      setCreated(key)
      setCreateOpen(false)
      setName('')
      setScopes(['read'])
    } catch {
      /* toast handled in hook */
    }
  }

  async function copyKey() {
    if (!created) return
    try {
      await navigator.clipboard.writeText(created.key)
      toast.success(t('settings.key_copied'))
    } catch {
      toast.error(t('common.could_not_copy'))
    }
  }

  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {t('settings.programmatic_access_to_the_stept_api')}
        </p>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="size-4" /> {t('settings.new_key')}
        </Button>
      </div>

      {keys.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : !keys.data || keys.data.length === 0 ? (
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <KeyRound />
            </EmptyMedia>
            <EmptyTitle>{t('settings.no_api_keys')}</EmptyTitle>
            <EmptyDescription>{t('settings.create_a_key_to_call_the')}</EmptyDescription>
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
                    <TableCell className="font-medium">{key.name}</TableCell>
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

      {/* Create dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('settings.new_api_key')}</DialogTitle>
            <DialogDescription>{t('settings.choose_the_scopes_this_key_should')}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-1.5">
              <Label htmlFor="key-name">{t('common.name')}</Label>
              <Input
                id="key-name"
                placeholder={t('settings.e_g_zapier_integration')}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label>{t('settings.scopes')}</Label>
              {SCOPES.map((scope) => (
                <label
                  key={scope.value}
                  className="flex items-center gap-2 text-sm"
                  htmlFor={`scope-${scope.value}`}
                >
                  <Checkbox
                    id={`scope-${scope.value}`}
                    checked={scopes.includes(scope.value)}
                    onCheckedChange={() => toggleScope(scope.value)}
                  />
                  <span className="font-medium">{scope.label}</span>
                  <span className="text-xs text-muted-foreground">{scope.hint}</span>
                </label>
              ))}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button onClick={submit} disabled={!name.trim() || scopes.length === 0 || create.isPending}>
              {create.isPending ? 'Creating…' : 'Create key'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reveal-once dialog */}
      <Dialog open={created !== null} onOpenChange={(open) => !open && setCreated(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('settings.copy_your_api_key')}</DialogTitle>
            <DialogDescription>
              {t('settings.this_is_the_only_time_the')}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 py-2">
            <div className="flex items-center gap-2 rounded-md border bg-muted/50 px-3 py-2">
              <code className="flex-1 truncate font-mono text-xs" data-testid="revealed-key">
                {created?.key}
              </code>
              <Button variant="ghost" size="icon" className="size-7" aria-label={t('settings.copy_key')} onClick={copyKey}>
                <Copy className="size-4" />
              </Button>
            </div>
            <p className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
              <TriangleAlert className="size-4" /> {t('settings.you_won_t_be_able_to')}
            </p>
          </div>
          <DialogFooter>
            <Button onClick={() => setCreated(null)}>{t('common.done')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
