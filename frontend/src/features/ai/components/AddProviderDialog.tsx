/** Add an AI provider: pick a kind, name it, and store a write-only API key. */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { currentWorkspaceId } from '@/stores/auth'

import { aiApi, aiKeys, type ProviderKind } from '../api'
import { PROVIDER_KINDS } from './status'

export function AddProviderDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const [kind, setKind] = useState<ProviderKind>('anthropic')
  const [name, setName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')

  const kindMeta = PROVIDER_KINDS.find((k) => k.value === kind)
  const needsBaseUrl = Boolean(kindMeta?.needsBaseUrl)
  const needsKey = kind !== 'mock' && kind !== 'ollama'

  const mutation = useMutation({
    mutationFn: () =>
      aiApi.createProvider({
        kind,
        name: name.trim() || (kindMeta?.label ?? kind),
        base_url: needsBaseUrl ? baseUrl.trim() || null : null,
        api_key: apiKey.trim() || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: aiKeys.providers(workspaceId) })
      queryClient.invalidateQueries({ queryKey: aiKeys.models(workspaceId) })
      toast.success('Provider added')
      setName('')
      setBaseUrl('')
      setApiKey('')
      onOpenChange(false)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not add provider'),
  })

  const canSubmit = (!needsBaseUrl || baseUrl.trim().length > 0) && !mutation.isPending

  return (
    <Dialog open={open} onOpenChange={(next) => !mutation.isPending && onOpenChange(next)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add AI provider</DialogTitle>
          <DialogDescription>
            Connect a model provider. Your API key is stored encrypted and never shown again.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="provider-kind">Provider</Label>
            <NativeSelect
              id="provider-kind"
              value={kind}
              onChange={(e) => setKind(e.target.value as ProviderKind)}
              className="w-full"
            >
              {PROVIDER_KINDS.map((k) => (
                <NativeSelectOption key={k.value} value={k.value}>
                  {k.label}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="provider-name">Display name</Label>
            <Input
              id="provider-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={kindMeta?.label ?? 'Provider'}
            />
          </div>
          {needsBaseUrl ? (
            <div className="grid gap-1.5">
              <Label htmlFor="provider-base-url">Base URL</Label>
              <Input
                id="provider-base-url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="http://localhost:11434/v1"
              />
            </div>
          ) : null}
          {needsKey ? (
            <div className="grid gap-1.5">
              <Label htmlFor="provider-key">API key</Label>
              <Input
                id="provider-key"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-…"
                autoComplete="off"
              />
            </div>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={!canSubmit}>
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            Add provider
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
