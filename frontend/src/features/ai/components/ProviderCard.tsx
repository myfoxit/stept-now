/** A single provider: connection state, test button, and catalog model manager. */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { CheckCircle2, Loader2, Plug, Plus, Star, Trash2, XCircle } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { cn } from '@/lib/utils'
import { currentWorkspaceId } from '@/stores/auth'

import { aiApi, aiKeys, type Provider, type ProviderTestResult } from '../api'
import { useCatalog, useProviderModels } from '../hooks'
import { providerKindLabel } from './status'

export function ProviderCard({ provider }: { provider: Provider }) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null)

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: aiKeys.providers(workspaceId) })
    queryClient.invalidateQueries({ queryKey: aiKeys.providerModels(workspaceId, provider.id) })
    queryClient.invalidateQueries({ queryKey: aiKeys.models(workspaceId) })
  }

  const testMutation = useMutation({
    mutationFn: () => aiApi.testProvider(provider.id),
    onSuccess: (result) => {
      setTestResult(result)
      if (result.ok) toast.success(`Connected in ${result.latency_ms} ms`)
      else toast.error(result.message)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Test failed'),
  })

  const deleteMutation = useMutation({
    mutationFn: () => aiApi.deleteProvider(provider.id),
    onSuccess: () => {
      invalidate()
      toast.success('Provider removed')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Delete failed'),
  })

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2 space-y-0">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium">{provider.name}</span>
            <Badge variant="outline">{providerKindLabel(provider.kind)}</Badge>
            {!provider.enabled ? <Badge variant="secondary">Disabled</Badge> : null}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {provider.has_key ? `Key ${provider.api_key_hint ?? '••••'}` : 'No API key'}
            {provider.base_url ? ` · ${provider.base_url}` : ''}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            size="sm"
            variant="outline"
            onClick={() => testMutation.mutate()}
            disabled={testMutation.isPending}
          >
            {testMutation.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Plug className="size-4" />
            )}
            Test
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="size-8 text-destructive"
            aria-label="Remove provider"
            onClick={() => deleteMutation.mutate()}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {testResult ? (
          <div
            className={cn(
              'flex items-center gap-2 rounded-md border px-3 py-2 text-sm',
              testResult.ok
                ? 'border-emerald-500/30 text-emerald-600 dark:text-emerald-400'
                : 'border-destructive/30 text-destructive'
            )}
          >
            {testResult.ok ? (
              <CheckCircle2 className="size-4" />
            ) : (
              <XCircle className="size-4" />
            )}
            <span>{testResult.message}</span>
            <span className="ml-auto text-xs tabular-nums text-muted-foreground">
              {testResult.latency_ms} ms
            </span>
          </div>
        ) : null}
        <ProviderModels provider={provider} onChanged={invalidate} />
      </CardContent>
    </Card>
  )
}

function ProviderModels({ provider, onChanged }: { provider: Provider; onChanged: () => void }) {
  const catalog = useCatalog()
  const models = useProviderModels(provider.id)
  const [custom, setCustom] = useState('')

  const enableMutation = useMutation({
    mutationFn: (body: {
      model_key: string
      display_name?: string | null
      modality?: 'chat' | 'embedding'
      context_window?: number | null
    }) => aiApi.createModel(provider.id, body),
    onSuccess: () => {
      onChanged()
      setCustom('')
      toast.success('Model enabled')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not enable model'),
  })

  const disableMutation = useMutation({
    mutationFn: (id: string) => aiApi.deleteModel(id),
    onSuccess: () => {
      onChanged()
      toast.success('Model removed')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not remove model'),
  })

  const defaultMutation = useMutation({
    mutationFn: (id: string) => aiApi.setDefaultModel(id),
    onSuccess: () => {
      onChanged()
      toast.success('Default model set')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not set default'),
  })

  const enabled = models.data ?? []
  const catalogModels = catalog.data?.[provider.kind] ?? []
  const enabledKeys = new Set(enabled.map((m) => m.model_key))

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium text-muted-foreground">Models</p>

      {catalogModels.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {catalogModels.map((model) => {
            const isEnabled = enabledKeys.has(model.model_key)
            return (
              <Button
                key={model.model_key}
                size="sm"
                variant={isEnabled ? 'secondary' : 'outline'}
                disabled={isEnabled || enableMutation.isPending}
                onClick={() =>
                  enableMutation.mutate({
                    model_key: model.model_key,
                    display_name: model.display_name,
                    modality: model.modality,
                    context_window: model.context_window,
                  })
                }
              >
                {isEnabled ? <CheckCircle2 className="size-3.5" /> : <Plus className="size-3.5" />}
                {model.display_name}
              </Button>
            )
          })}
        </div>
      ) : null}

      {/* Custom model entry — required for ollama / openai-compatible / mock */}
      <div className="flex gap-2">
        <Input
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          placeholder="Add a custom model key…"
          aria-label={`Custom model for ${provider.name}`}
          className="h-8"
        />
        <Button
          size="sm"
          variant="outline"
          disabled={!custom.trim() || enableMutation.isPending}
          onClick={() => enableMutation.mutate({ model_key: custom.trim() })}
        >
          Add
        </Button>
      </div>

      {models.isLoading ? (
        <Spinner className="size-4" />
      ) : enabled.length > 0 ? (
        <ul className="space-y-1">
          {enabled.map((model) => (
            <li
              key={model.id}
              className="flex items-center gap-2 rounded-md border px-2 py-1 text-sm"
            >
              <span className="truncate">{model.display_name}</span>
              <Badge variant="outline" className="text-[10px] capitalize">
                {model.modality}
              </Badge>
              {model.is_default ? (
                <Badge className="gap-1 text-[10px]">
                  <Star className="size-3" /> Default
                </Badge>
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto h-6 px-2 text-xs"
                  onClick={() => defaultMutation.mutate(model.id)}
                >
                  Set default
                </Button>
              )}
              <Button
                size="icon"
                variant="ghost"
                className={cn('size-6', model.is_default && 'ml-auto')}
                aria-label={`Remove ${model.display_name}`}
                onClick={() => disableMutation.mutate(model.id)}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">No models enabled yet.</p>
      )}
    </div>
  )
}
