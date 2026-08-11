/** Create or edit a custom action (an HTTP tool the agent can call). */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Loader2, Plus, X } from 'lucide-react'
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
import { Textarea } from '@/components/ui/textarea'
import { currentWorkspaceId } from '@/stores/auth'

import { aiApi, aiKeys, type CustomAction } from '../api'
import { t } from '@/i18n'

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
const DEFAULT_SCHEMA = `{
  "type": "object",
  "properties": {
    "order_id": { "type": "string" }
  },
  "required": ["order_id"]
}`

interface HeaderRow {
  key: string
  value: string
}

export function ActionDialog({
  action,
  open,
  onOpenChange,
}: {
  action?: CustomAction | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const workspaceId = currentWorkspaceId()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [method, setMethod] = useState('POST')
  const [url, setUrl] = useState('')
  const [headers, setHeaders] = useState<HeaderRow[]>([])
  const [bodyTemplate, setBodyTemplate] = useState('')
  const [schema, setSchema] = useState(DEFAULT_SCHEMA)
  const [timeoutS, setTimeoutS] = useState(10)
  const [schemaError, setSchemaError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setName(action?.name ?? '')
    setDescription(action?.description ?? '')
    setMethod(action?.method ?? 'POST')
    setUrl(action?.url ?? '')
    setHeaders((action?.header_names ?? []).map((key) => ({ key, value: '' })))
    setBodyTemplate(action?.body_template ?? '')
    setSchema(action ? JSON.stringify(action.params_schema, null, 2) : DEFAULT_SCHEMA)
    setTimeoutS(action?.timeout_s ?? 10)
    setSchemaError(null)
  }, [open, action])

  const mutation = useMutation({
    mutationFn: () => {
      let parsedSchema: Record<string, unknown> = {}
      try {
        parsedSchema = schema.trim() ? JSON.parse(schema) : {}
      } catch {
        throw new Error('Params schema is not valid JSON')
      }
      const headerObj = Object.fromEntries(
        headers.filter((h) => h.key.trim() && h.value.trim()).map((h) => [h.key.trim(), h.value])
      )
      const body: Record<string, unknown> = {
        name: name.trim(),
        description,
        method,
        url: url.trim(),
        body_template: bodyTemplate.trim() || null,
        params_schema: parsedSchema,
        timeout_s: timeoutS,
      }
      // Only send headers when the user provided values (avoids wiping stored secrets).
      if (Object.keys(headerObj).length > 0) body.headers = headerObj
      return action ? aiApi.updateAction(action.id, body) : aiApi.createAction(body)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: aiKeys.actions(workspaceId) })
      toast.success(action ? 'Action updated' : 'Action created')
      onOpenChange(false)
    },
    onError: (e) => {
      const msg = e instanceof ApiError || e instanceof Error ? e.message : 'Could not save action'
      if (msg.includes('schema')) setSchemaError(msg)
      toast.error(msg)
    },
  })

  return (
    <Dialog open={open} onOpenChange={(next) => !mutation.isPending && onOpenChange(next)}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{action ? 'Edit action' : 'New custom action'}</DialogTitle>
          <DialogDescription>
            {t('ai.let_the_agent_call_an_external')}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="action-name">{t('common.name')}</Label>
              <Input
                id="action-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('ai.lookup_order')}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="action-timeout">Timeout (s)</Label>
              <Input
                id="action-timeout"
                type="number"
                min={1}
                max={60}
                value={timeoutS}
                onChange={(e) => setTimeoutS(Number(e.target.value))}
              />
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="action-desc">Description (shown to the model)</Label>
            <Textarea
              id="action-desc"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t('ai.look_up_the_status_of_a')}
            />
          </div>
          <div className="grid grid-cols-[110px_1fr] gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="action-method">{t('ai.method')}</Label>
              <NativeSelect
                id="action-method"
                value={method}
                onChange={(e) => setMethod(e.target.value)}
                className="w-full"
              >
                {METHODS.map((m) => (
                  <NativeSelectOption key={m} value={m}>
                    {m}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="action-url">URL</Label>
              <Input
                id="action-url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://api.example.com/orders/{order_id}"
              />
            </div>
          </div>

          <div className="grid gap-1.5">
            <Label>{t('ai.headers')}</Label>
            {action ? (
              <p className="text-xs text-muted-foreground">
                {t('ai.stored_values_are_hidden_re_enter')}
              </p>
            ) : null}
            {headers.map((header, i) => (
              <div key={i} className="flex gap-2">
                <Input
                  aria-label={`Header ${i + 1} name`}
                  value={header.key}
                  onChange={(e) =>
                    setHeaders((prev) => prev.map((h, idx) => (idx === i ? { ...h, key: e.target.value } : h)))
                  }
                  placeholder={t('ai.authorization')}
                  className="h-8"
                />
                <Input
                  aria-label={`Header ${i + 1} value`}
                  type="password"
                  value={header.value}
                  onChange={(e) =>
                    setHeaders((prev) => prev.map((h, idx) => (idx === i ? { ...h, value: e.target.value } : h)))
                  }
                  placeholder="Bearer …"
                  className="h-8"
                />
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-8 shrink-0"
                  aria-label={`Remove header ${i + 1}`}
                  onClick={() => setHeaders((prev) => prev.filter((_, idx) => idx !== i))}
                >
                  <X className="size-4" />
                </Button>
              </div>
            ))}
            <Button
              size="sm"
              variant="outline"
              className="w-fit"
              onClick={() => setHeaders((prev) => [...prev, { key: '', value: '' }])}
            >
              <Plus className="size-4" /> {t('ai.add_header')}
            </Button>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="action-body">Body template (optional)</Label>
            <Textarea
              id="action-body"
              rows={3}
              value={bodyTemplate}
              onChange={(e) => setBodyTemplate(e.target.value)}
              placeholder={'{"id": "{order_id}"}'}
              className="font-mono text-xs"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="action-schema">Params schema (JSON)</Label>
            <Textarea
              id="action-schema"
              rows={6}
              value={schema}
              onChange={(e) => {
                setSchema(e.target.value)
                setSchemaError(null)
              }}
              className="font-mono text-xs"
              aria-invalid={Boolean(schemaError)}
            />
            {schemaError ? <p className="text-xs text-destructive">{schemaError}</p> : null}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={mutation.isPending}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!name.trim() || !url.trim() || mutation.isPending}
          >
            {mutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
            Save action
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
